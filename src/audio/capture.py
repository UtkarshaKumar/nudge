"""
AudioCapture: Records audio from a virtual loopback device (BlackHole 2ch).

Threading model:
  sounddevice callback   → audio_queue (raw frames, never blocks)
  CollectionThread       → drains queue, accumulates buffer, saves 30s chunks
  (caller's thread)      → on_chunk_saved callback fires in a daemon thread

This separation ensures audio capture never stalls due to disk I/O or
downstream processing.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from ..config import AudioConfig
from .devices import find_device_id

logger = logging.getLogger(__name__)

CHUNK_DTYPE = "float32"
# Queue capacity: 60 seconds of audio at 16kHz in 0.1s blocks
_QUEUE_MAXSIZE = 600


class AudioCapture:
    """
    Captures audio from a BlackHole loopback device.

    Usage:
        capture = AudioCapture(config, session_dir)
        capture.on_chunk_saved(callback)   # optional
        capture.start()
        # ... meeting runs ...
        capture.stop()
        paths = capture.get_chunk_paths()
    """

    def __init__(self, config: AudioConfig, session_dir: Path):
        self.config = config
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._device_id = self._resolve_device()
        self._audio_queue: queue.Queue[Optional[np.ndarray]] = queue.Queue(
            maxsize=_QUEUE_MAXSIZE
        )
        self._chunk_buffer: list[np.ndarray] = []
        self._chunk_index = 0
        self._frames_per_chunk = config.sample_rate * config.chunk_duration

        self._is_recording = False
        self._stream_thread: Optional[threading.Thread] = None
        self._collection_thread: Optional[threading.Thread] = None
        self._on_chunk_saved: Optional[Callable[[Path], None]] = None

    def on_chunk_saved(self, callback: Callable[[Path], None]) -> None:
        """Register a callback invoked (in a daemon thread) each time a chunk is saved."""
        self._on_chunk_saved = callback

    def start(self) -> None:
        if self._is_recording:
            raise RuntimeError("Already recording. Call stop() first.")

        self._is_recording = True
        self._chunk_index = 0
        self._chunk_buffer = []

        self._collection_thread = threading.Thread(
            target=self._collection_loop,
            daemon=True,
            name="nudge-audio-collect",
        )
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name="nudge-audio-stream",
        )
        self._collection_thread.start()
        self._stream_thread.start()
        logger.info(f"Audio capture started — device: {self.config.device}")

    def stop(self) -> None:
        """Stop recording and flush any remaining buffered audio to disk."""
        self._is_recording = False

        # Unblock the collection thread
        self._audio_queue.put(None)

        if self._stream_thread:
            self._stream_thread.join(timeout=3)
        if self._collection_thread:
            self._collection_thread.join(timeout=5)

        logger.info("Audio capture stopped")

    def get_chunk_paths(self) -> list[Path]:
        """Return all saved chunk files in recording order."""
        return sorted(self.session_dir.glob("chunk_*.wav"))

    # ------------------------------------------------------------------
    # Internal threads
    # ------------------------------------------------------------------

    def _stream_loop(self) -> None:
        """Runs the sounddevice InputStream. Exits when _is_recording is False."""
        try:
            with sd.InputStream(
                device=self._device_id,
                channels=self.config.channels,
                samplerate=self.config.sample_rate,
                dtype=CHUNK_DTYPE,
                latency="low",
                callback=self._audio_callback,
            ):
                while self._is_recording:
                    time.sleep(0.05)
        except Exception as exc:
            logger.error(f"Audio stream error: {exc}")
            self._is_recording = False

    def _collection_loop(self) -> None:
        """Drains the audio queue, accumulates buffer, saves chunks."""
        while True:
            try:
                frame = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                if not self._is_recording:
                    break
                continue

            if frame is None:
                # Sentinel — flush and exit
                self._flush()
                break

            self._chunk_buffer.append(frame)
            total = sum(len(c) for c in self._chunk_buffer)
            if total >= self._frames_per_chunk:
                self._save_chunk()

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice callback — must be fast, no blocking allowed."""
        if status:
            logger.warning(f"Audio status flags: {status}")
        try:
            self._audio_queue.put_nowait(indata.copy())
        except queue.Full:
            logger.warning("Audio queue full — dropping frame (system load too high)")

    # ------------------------------------------------------------------
    # Chunk management
    # ------------------------------------------------------------------

    def _save_chunk(self) -> Optional[Path]:
        if not self._chunk_buffer:
            return None

        audio = np.concatenate(self._chunk_buffer, axis=0)
        self._chunk_buffer = []

        chunk_path = self.session_dir / f"chunk_{self._chunk_index:04d}.wav"
        sf.write(str(chunk_path), audio, self.config.sample_rate)
        self._chunk_index += 1

        logger.debug(f"Saved {chunk_path.name} ({len(audio) / self.config.sample_rate:.1f}s)")

        if self._on_chunk_saved:
            threading.Thread(
                target=self._on_chunk_saved,
                args=(chunk_path,),
                daemon=True,
                name=f"nudge-transcribe-{self._chunk_index}",
            ).start()

        return chunk_path

    def _flush(self) -> None:
        """Save any remaining audio in the buffer."""
        if self._chunk_buffer:
            self._save_chunk()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _resolve_device(self) -> int:
        device_id = find_device_id(self.config.device)
        if device_id is None:
            import sounddevice as sd_inner
            available = [
                f"  [{d['id']}] {d['name']}"
                for d in [
                    {"id": i, "name": dev["name"]}
                    for i, dev in enumerate(sd_inner.query_devices())
                    if dev["max_input_channels"] > 0
                ]
            ]
            raise RuntimeError(
                f"Audio device '{self.config.device}' not found.\n"
                f"Available input devices:\n" + "\n".join(available) + "\n\n"
                "Run 'nudge doctor' to diagnose setup issues."
            )
        return device_id
