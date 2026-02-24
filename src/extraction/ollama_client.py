"""
Ollama client: action item extraction + meeting analysis.

Uses tenacity for exponential backoff retries.
Handles long transcripts via overlapping sliding windows.
Auto-starts Ollama daemon if not running.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from typing import Any, Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import OllamaConfig
from .dedup import deduplicate
from .prompts import CURRENT_ACTIONS_PROMPT, CURRENT_ANALYSIS_PROMPT, CURRENT_DIGEST_PROMPT

logger = logging.getLogger(__name__)

# Approximate characters per token for window splitting
_CHARS_PER_TOKEN = 4
_WINDOW_TOKENS = 3000
_OVERLAP_TOKENS = 300


# ── Ollama daemon management ──────────────────────────────────────────────────

def ensure_ollama_running(host: str = "http://localhost:11434") -> None:
    """Start Ollama if it isn't responding. Waits up to 30s for startup."""
    import ollama as sdk
    client = sdk.Client(host=host)
    try:
        client.list()
        return  # already running
    except Exception:
        pass

    logger.info("Ollama not running — starting it...")
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(30):
        time.sleep(1)
        try:
            client.list()
            logger.info("Ollama started")
            return
        except Exception:
            pass

    raise RuntimeError(
        "Could not start Ollama. Run 'ollama serve' in a separate terminal "
        "and try again."
    )


# ── Core LLM call ─────────────────────────────────────────────────────────────

def _call_ollama(
    client,
    model: str,
    prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """Make a single Ollama generate call. Returns raw response text."""
    response = client.generate(
        model=model,
        prompt=prompt,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    return response["response"].strip()


def _parse_json(raw: str) -> Any:
    """
    Robust JSON extraction from LLM output.
    Handles models that wrap JSON in markdown fences or prose.
    """
    # Direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences if present
    fenced = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass

    # Extract first JSON object/array in the text
    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        match = re.search(pattern, raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    logger.warning(f"Failed to parse LLM JSON. Raw output (first 300 chars): {raw[:300]}")
    return None


# ── Window splitting ──────────────────────────────────────────────────────────

def _split_windows(text: str, window_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= window_chars:
        return [text]

    windows = []
    start = 0
    while start < len(text):
        end = start + window_chars
        if end < len(text):
            # Try to break on a sentence boundary
            boundary = text.rfind(". ", start, end)
            if boundary > start + window_chars // 2:
                end = boundary + 1
        windows.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap_chars

    return windows


# ── Public extractors ─────────────────────────────────────────────────────────

class ActionExtractor:
    """
    Extracts structured action items from meeting transcripts.
    Handles transcripts of any length via overlapping windows.
    """

    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            import ollama as sdk
            self._client = sdk.Client(host=self.config.host)
        return self._client

    def extract(self, transcript: str) -> list[dict]:
        """
        Extract action items from a full meeting transcript.
        Returns deduplicated list sorted by confidence descending.
        """
        ensure_ollama_running(self.config.host)

        window_chars = _WINDOW_TOKENS * _CHARS_PER_TOKEN
        overlap_chars = _OVERLAP_TOKENS * _CHARS_PER_TOKEN
        windows = _split_windows(transcript, window_chars, overlap_chars)

        logger.info(f"Extracting actions from {len(windows)} window(s)")

        all_actions: list[dict] = []
        for i, window in enumerate(windows):
            logger.debug(f"Processing action window {i + 1}/{len(windows)}")
            actions = self._extract_window(window)
            all_actions.extend(actions)

        deduped = deduplicate(all_actions)
        logger.info(f"Found {len(deduped)} unique action items")
        return deduped

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _extract_window(self, window_text: str) -> list[dict]:
        # Inject system instructions to force strictness
        system_instruction = (
            "You are an AI that strictly follows instructions. "
            "If a meeting transcript does not contain any explicit commitments or action items assigned to a person, "
            "you MUST output an empty JSON array `[]` and nothing else. "
            "Do NOT invent tasks. Do not summarize the meeting as tasks."
        )
        raw = self._get_client().generate(
            model=self.config.model,
            prompt=CURRENT_ACTIONS_PROMPT.format(transcript=window_text),
            system=system_instruction,
            options={
                "temperature": 0.0,
                "num_predict": 2048,
            },
        )["response"].strip()
        
        result = _parse_json(raw)
        if isinstance(result, list):
            # Validate minimal structure
            return [
                a for a in result
                if isinstance(a, dict) and a.get("task", "").strip()
            ]
        return []


class MeetingAnalyzer:
    """
    Generates meeting summary, decisions, participants, and topics.
    Used for the Word document header and weekly digest.
    """

    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            import ollama as sdk
            self._client = sdk.Client(host=self.config.host)
        return self._client

    def analyze(self, transcript: str) -> dict:
        """
        Returns dict with: title, summary, decisions, participants, topics.
        Falls back to safe defaults on any error.
        """
        ensure_ollama_running(self.config.host)

        # Use only the first 3K tokens for analysis (summary doesn't need full context)
        window_chars = 3000 * _CHARS_PER_TOKEN
        excerpt = transcript[:window_chars]

        try:
            raw = self._analyze_excerpt(excerpt)
            result = _parse_json(raw)
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning(f"Meeting analysis failed: {e}")

        return {
            "title": "Meeting",
            "summary": "",
            "decisions": [],
            "participants": [],
            "topics": [],
        }

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _analyze_excerpt(self, excerpt: str) -> str:
        return _call_ollama(
            self._get_client(),
            self.config.model,
            CURRENT_ANALYSIS_PROMPT.format(transcript=excerpt),
            temperature=0.2,
            max_tokens=1024,
        )


class DigestGenerator:
    """Generates a weekly summary digest from multiple meeting analyses."""

    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            import ollama as sdk
            self._client = sdk.Client(host=self.config.host)
        return self._client

    def generate(self, meeting_summaries: list[str]) -> dict:
        """Takes a list of meeting summary strings, returns digest dict."""
        ensure_ollama_running(self.config.host)

        combined = "\n\n---\n\n".join(meeting_summaries)
        try:
            raw = _call_ollama(
                self._get_client(),
                self.config.model,
                CURRENT_DIGEST_PROMPT.format(meeting_summaries=combined),
                temperature=0.3,
            )
            result = _parse_json(raw)
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning(f"Digest generation failed: {e}")

        return {
            "week_summary": "",
            "key_themes": [],
            "critical_actions": [],
            "wins": [],
        }
