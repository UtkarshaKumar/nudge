"""
Deduplication for action items extracted from overlapping transcript windows.

When a long transcript is split into overlapping windows, the same action
may be extracted twice. This module removes near-duplicates using fuzzy
string matching on the task description.
"""
from __future__ import annotations

import difflib
import re


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def similarity(a: str, b: str) -> float:
    """Character-level sequence similarity between two strings (0–1)."""
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def deduplicate(actions: list[dict], threshold: float = 0.80) -> list[dict]:
    """
    Remove near-duplicate action items.

    Keeps the version with higher confidence when duplicates are found.
    Two actions are considered duplicates if their task descriptions
    have similarity >= threshold.
    """
    if not actions:
        return []

    # Sort by confidence descending so we keep the best version of each dup
    sorted_actions = sorted(actions, key=lambda a: a.get("confidence", 0), reverse=True)

    unique: list[dict] = []
    for candidate in sorted_actions:
        task = candidate.get("task", "")
        is_dup = any(
            similarity(task, kept.get("task", "")) >= threshold
            for kept in unique
        )
        if not is_dup:
            unique.append(candidate)

    return unique
