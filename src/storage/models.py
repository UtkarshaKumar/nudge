"""
Data models for sessions, transcript chunks, and action items.
Plain dataclasses — no ORM, keeps the storage layer simple.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionStatus(str, Enum):
    RECORDING = "recording"
    STOPPED = "stopped"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"


class ActionStatus(str, Enum):
    PENDING = "pending"
    ADDED = "added"
    SKIPPED = "skipped"
    ERROR = "error"


def _short_id() -> str:
    return str(uuid.uuid4())[:8]


@dataclass
class Session:
    title: str = ""
    status: SessionStatus = SessionStatus.RECORDING
    started_at: datetime = field(default_factory=datetime.now)
    stopped_at: Optional[datetime] = None
    audio_dir: str = ""
    transcript_path: Optional[str] = None
    notes_path: Optional[str] = None
    model_whisper: str = ""
    model_llm: str = ""
    id: str = field(default_factory=_short_id)

    @property
    def duration_seconds(self) -> int:
        if self.stopped_at:
            return int((self.stopped_at - self.started_at).total_seconds())
        return int((datetime.now() - self.started_at).total_seconds())


@dataclass
class TranscriptChunkModel:
    session_id: str
    chunk_index: int
    text: str
    audio_file: str = ""
    started_at_offset: float = 0.0
    ended_at_offset: float = 0.0
    language: str = "en"
    confidence: float = 1.0
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ActionItem:
    session_id: str
    task: str
    confidence: float
    id: str = field(default_factory=_short_id)
    assignee: Optional[str] = None
    deadline_raw: Optional[str] = None
    deadline_parsed: Optional[str] = None
    context: Optional[str] = None
    source_quote: Optional[str] = None
    reminder_id: Optional[str] = None
    status: ActionStatus = ActionStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
