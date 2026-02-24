"""
WhisperEngine: faster-whisper wrapper for local speech-to-text.

Uses CTranslate2 backend which automatically uses Apple Metal on M1/M2/M3.
VAD filter skips silence — typically 40–60% faster on real meeting audio.
Model is loaded once and reused across all chunks in a session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import WhisperConfig

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    """A single transcribed segment from a chunk."""
    start: float    # seconds from chunk start
    end: float
    text: str
    avg_logprob: float = -0.5  # higher (less negative) = more confident


@dataclass
class TranscriptChunk:
    """Result of transcribing one 30-second audio chunk."""
    chunk_index: int
    audio_file: Path
    segments: list[Segment] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 1.0

    @property
    def text(self) -> str:
        """Full text of this chunk, space-joined."""
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class WhisperEngine:
    """
    Wraps faster-whisper. Load model once, transcribe many chunks.

    Not thread-safe: call transcribe() from a single thread only.
    (The AudioCapture's TranscriptionThread handles this correctly.)
    """

    def __init__(self, config: WhisperConfig):
        self.config = config
        self._model = None

    def load(self) -> None:
        """Load model into memory. Safe to call multiple times (no-op after first load)."""
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        logger.info(f"Loading Whisper '{self.config.model}' (compute={self.config.compute_type})")
        self._model = WhisperModel(
            self.config.model,
            device="auto",           # uses Metal on Apple Silicon automatically
            compute_type=self.config.compute_type,
        )
        logger.info("Whisper model ready")

    def transcribe(self, audio_path: Path, chunk_index: int = 0) -> TranscriptChunk:
        """
        Transcribe a single audio chunk.
        Loads model on first call if not already loaded.
        """
        self.load()

        language = self.config.language if self.config.language != "auto" else None

        segments_iter, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            language=language,
            vad_filter=self.config.vad_filter,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=False,
        )

        segments = [
            Segment(
                start=seg.start,
                end=seg.end,
                text=seg.text,
                avg_logprob=seg.avg_logprob,
            )
            for seg in segments_iter
        ]

        result = TranscriptChunk(
            chunk_index=chunk_index,
            audio_file=audio_path,
            segments=segments,
            language=info.language,
            language_probability=info.language_probability,
        )

        if not result.is_empty:
            logger.debug(
                f"chunk_{chunk_index:04d}: {len(segments)} segments, "
                f"lang={info.language} ({info.language_probability:.0%})"
            )

        return result

    def transcribe_all(self, chunk_paths: list[Path]) -> list[TranscriptChunk]:
        """Transcribe multiple chunks sequentially. Returns results in order."""
        results = []
        for path in sorted(chunk_paths):
            index = int(path.stem.split("_")[1])
            logger.info(f"Transcribing {path.name} ({index + 1}/{len(chunk_paths)})")
            results.append(self.transcribe(path, chunk_index=index))
        return results
