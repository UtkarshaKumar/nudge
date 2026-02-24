"""
MeetingWatcher: polls for active meetings and auto-starts/stops recording.

State machine:
  IDLE ──(meeting detected, grace start)──► CONFIRMING
  CONFIRMING ──(still active after 15s)──► RECORDING
  CONFIRMING ──(gone within 15s)──► IDLE       (avoids false starts)
  RECORDING ──(meeting gone, grace stop)──► COOLING_DOWN
  COOLING_DOWN ──(still gone after 60s)──► PROCESSING
  COOLING_DOWN ──(meeting back within 60s)──► RECORDING  (handles brief drops)
  PROCESSING ──(done)──► IDLE

The watcher runs as a background loop, typically via 'nudge watch'
or as a macOS LaunchAgent started at login.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import threading
import time
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from ..audio.capture import AudioCapture
from ..audio.devices import check_disk_space, is_blackhole_available
from ..config import Config, load_config
from ..extraction.ollama_client import ActionExtractor, MeetingAnalyzer
from ..integrations.reminders import add_actions_to_reminders
from ..integrations.word_notes import generate_meeting_notes, open_notes
from ..storage.db import Database
from ..storage.models import (
    ActionItem,
    ActionStatus,
    Session,
    SessionStatus,
    TranscriptChunkModel,
)
from ..transcription.whisper_engine import WhisperEngine
from .detector import MeetingState, detect_meeting

logger = logging.getLogger(__name__)


class WatcherState(Enum):
    IDLE = auto()
    CONFIRMING = auto()        # meeting detected, waiting to confirm it's real
    RECORDING = auto()
    COOLING_DOWN = auto()      # meeting ended, waiting before processing
    PROCESSING = auto()


# Tuning constants
POLL_INTERVAL_SECONDS = 5
START_GRACE_SECONDS = 15      # confirm meeting is real before recording
STOP_GRACE_SECONDS = 60       # wait this long after meeting ends before processing
LOCK_FILE = Path.home() / ".nudge" / "nudge.lock"


class MeetingWatcher:
    """
    Polls for active meetings. Auto-starts recording on join, auto-stops on leave.
    Designed to run forever (until SIGTERM).
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or load_config()
        self.db = Database(self.config.storage.db_path)

        self._state = WatcherState.IDLE
        self._state_entered_at: float = 0.0

        self._session: Optional[Session] = None
        self._capture: Optional[AudioCapture] = None
        self._engine: Optional[WhisperEngine] = None
        self._transcription_queue: queue.Queue = queue.Queue()
        self._transcription_thread: Optional[threading.Thread] = None

        self._running = False
        self._transcript_offset = 0.0
        self._last_platform: str = ""

    def run(self) -> None:
        """Main watcher loop. Blocks until stop() is called or SIGTERM received."""
        self._running = True
        self._setup_signal_handlers()

        logger.info("nudge watcher started — watching for meetings")

        # Check prerequisites once at startup
        if not is_blackhole_available(self.config.audio.device):
            logger.error(
                f"BlackHole device '{self.config.audio.device}' not found. "
                "Run install.sh to fix this."
            )
            return

        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.error(f"Watcher tick error: {exc}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
        if self._state == WatcherState.RECORDING:
            self._stop_recording()

    # ── State machine ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        meeting = detect_meeting()
        now = time.monotonic()
        elapsed = now - self._state_entered_at

        if self._state == WatcherState.IDLE:
            if meeting.active:
                logger.info(
                    f"Meeting detected: {meeting.platform} — confirming in {START_GRACE_SECONDS}s"
                )
                self._last_platform = meeting.platform
                self._enter_state(WatcherState.CONFIRMING)

        elif self._state == WatcherState.CONFIRMING:
            if not meeting.active:
                logger.info("Meeting disappeared during confirmation — returning to idle")
                self._enter_state(WatcherState.IDLE)
            elif elapsed >= START_GRACE_SECONDS:
                logger.info(f"Confirmed meeting on {meeting.platform} — starting recording")
                self._start_recording(meeting)
                self._enter_state(WatcherState.RECORDING)

        elif self._state == WatcherState.RECORDING:
            if not meeting.active:
                logger.info(
                    f"Meeting ended — waiting {STOP_GRACE_SECONDS}s before processing"
                )
                self._enter_state(WatcherState.COOLING_DOWN)

        elif self._state == WatcherState.COOLING_DOWN:
            if meeting.active:
                # Meeting came back (rejoined / brief drop)
                logger.info("Meeting rejoined — continuing recording")
                self._enter_state(WatcherState.RECORDING)
            elif elapsed >= STOP_GRACE_SECONDS:
                self._stop_recording()
                self._enter_state(WatcherState.PROCESSING)
                self._process_session()
                self._enter_state(WatcherState.IDLE)

    def _enter_state(self, state: WatcherState) -> None:
        self._state = state
        self._state_entered_at = time.monotonic()

    # ── Recording lifecycle ───────────────────────────────────────────────────

    def _start_recording(self, meeting: MeetingState) -> None:
        if LOCK_FILE.exists():
            logger.warning("Lock file exists — skipping auto-start (manual session running?)")
            return

        # Auto-cleanup old audio on new session
        if self.config.storage.auto_delete_audio_days > 0:
            self.db.cleanup_old_audio(self.config.storage.auto_delete_audio_days)

        # Determine session title: use meeting title if available, else platform name
        title = meeting.title or meeting.platform or "Meeting"
        date_prefix = datetime.now().strftime("%b %d %H:%M")
        session_title = f"{title} — {date_prefix}"

        self._session = Session(
            title=session_title,
            model_whisper=self.config.whisper.model,
            model_llm=self.config.ollama.model,
        )
        sessions_dir = self.config.sessions_path / self._session.id
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._session.audio_dir = str(sessions_dir)
        self.db.create_session(self._session)

        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(self._session.id)

        # Load Whisper
        self._engine = WhisperEngine(self.config.whisper)
        self._engine.load()

        # Start transcription worker
        self._transcript_offset = 0.0
        self._transcription_queue = queue.Queue()
        self._transcription_thread = threading.Thread(
            target=self._transcription_worker,
            daemon=True,
            name="nudge-watcher-transcribe",
        )
        self._transcription_thread.start()

        # Start audio capture
        self._capture = AudioCapture(self.config.audio, sessions_dir)
        self._capture.on_chunk_saved(lambda p: self._transcription_queue.put(p))
        self._capture.start()

        logger.info(f"Recording started — session {self._session.id}: {session_title}")

    def _stop_recording(self) -> None:
        if self._capture:
            self._capture.stop()
            self._capture = None

        # Drain transcription queue
        if self._transcription_queue:
            self._transcription_queue.put(None)
        if self._transcription_thread:
            self._transcription_thread.join(timeout=120)
            self._transcription_thread = None

        if self._session:
            self.db.update_session_status(
                self._session.id,
                SessionStatus.STOPPED,
                stopped_at=datetime.now(),
            )

        LOCK_FILE.unlink(missing_ok=True)
        logger.info(f"Recording stopped — session {self._session.id if self._session else '?'}")

    def _process_session(self) -> None:
        if not self._session:
            return

        session_id = self._session.id
        logger.info(f"Processing session {session_id}")

        try:
            from ..cli.app import _process_session
            _process_session(session_id, self.config, self.db)
        except Exception as exc:
            logger.error(f"Session processing failed: {exc}", exc_info=True)
            self.db.update_session_status(session_id, SessionStatus.ERROR)

        self._session = None

    # ── Transcription worker ──────────────────────────────────────────────────

    def _transcription_worker(self) -> None:
        while True:
            chunk_path = self._transcription_queue.get()
            if chunk_path is None:
                break
            try:
                if self._engine and self._session:
                    index = int(chunk_path.stem.split("_")[1])
                    result = self._engine.transcribe(chunk_path, chunk_index=index)
                    if not result.is_empty:
                        self.db.save_chunk(
                            TranscriptChunkModel(
                                session_id=self._session.id,
                                chunk_index=result.chunk_index,
                                audio_file=str(chunk_path),
                                text=result.text,
                                started_at_offset=self._transcript_offset,
                                ended_at_offset=self._transcript_offset + self.config.audio.chunk_duration,
                                language=result.language,
                                confidence=result.language_probability,
                            )
                        )
                        logger.debug(f"Transcribed chunk {index}: {result.text[:60]}...")
                    self._transcript_offset += self.config.audio.chunk_duration
            except Exception as exc:
                logger.error(f"Transcription error: {exc}")

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _setup_signal_handlers(self) -> None:
        def _handle_sigterm(signum, frame):
            logger.info("SIGTERM received — shutting down watcher")
            self.stop()

        signal.signal(signal.SIGTERM, _handle_sigterm)
        signal.signal(signal.SIGINT, _handle_sigterm)
