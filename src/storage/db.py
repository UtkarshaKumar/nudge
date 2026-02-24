"""
SQLite storage layer for nudge.

Uses WAL journal mode for better concurrent read performance.
Schema migrations are applied automatically on startup.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .models import (
    ActionItem,
    ActionStatus,
    Session,
    SessionStatus,
    TranscriptChunkModel,
)

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'recording',
    started_at      TEXT NOT NULL,
    stopped_at      TEXT,
    audio_dir       TEXT NOT NULL DEFAULT '',
    transcript_path TEXT,
    notes_path      TEXT,
    model_whisper   TEXT NOT NULL DEFAULT '',
    model_llm       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcript_chunks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES sessions(id),
    chunk_index         INTEGER NOT NULL,
    audio_file          TEXT NOT NULL DEFAULT '',
    text                TEXT NOT NULL DEFAULT '',
    started_at_offset   REAL NOT NULL DEFAULT 0,
    ended_at_offset     REAL NOT NULL DEFAULT 0,
    language            TEXT NOT NULL DEFAULT 'en',
    confidence          REAL NOT NULL DEFAULT 1.0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS action_items (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    task            TEXT NOT NULL,
    assignee        TEXT,
    deadline_raw    TEXT,
    deadline_parsed TEXT,
    context         TEXT,
    source_quote    TEXT,
    confidence      REAL NOT NULL DEFAULT 0,
    reminder_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_session   ON transcript_chunks(session_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_actions_session  ON action_items(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, session: Session) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, title, status, started_at, audio_dir, model_whisper, model_llm)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.title,
                    session.status.value,
                    session.started_at.isoformat(),
                    session.audio_dir,
                    session.model_whisper,
                    session.model_llm,
                ),
            )

    def update_session_status(
        self,
        session_id: str,
        status: SessionStatus,
        stopped_at: Optional[datetime] = None,
        transcript_path: Optional[str] = None,
        notes_path: Optional[str] = None,
    ) -> None:
        updates = ["status = ?"]
        params: list = [status.value]

        if stopped_at:
            updates.append("stopped_at = ?")
            params.append(stopped_at.isoformat())
        if transcript_path:
            updates.append("transcript_path = ?")
            params.append(transcript_path)
        if notes_path:
            updates.append("notes_path = ?")
            params.append(notes_path)

        params.append(session_id)

        with self._conn() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return _row_to_session(row) if row else None

    def list_sessions(self, limit: int = 20) -> list[Session]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_session(r) for r in rows]

    def get_incomplete_sessions(self) -> list[Session]:
        """Sessions stuck in recording/stopped/processing state."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions "
                "WHERE status IN ('recording', 'stopped', 'processing') "
                "ORDER BY started_at DESC"
            ).fetchall()
            return [_row_to_session(r) for r in rows]

    def get_sessions_in_range(self, start: datetime, end: datetime) -> list[Session]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE started_at BETWEEN ? AND ? ORDER BY started_at",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            return [_row_to_session(r) for r in rows]

    # ------------------------------------------------------------------
    # Transcript chunks
    # ------------------------------------------------------------------

    def save_chunk(self, chunk: TranscriptChunkModel) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO transcript_chunks
                   (session_id, chunk_index, audio_file, text,
                    started_at_offset, ended_at_offset, language, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chunk.session_id,
                    chunk.chunk_index,
                    chunk.audio_file,
                    chunk.text,
                    chunk.started_at_offset,
                    chunk.ended_at_offset,
                    chunk.language,
                    chunk.confidence,
                ),
            )

    def get_transcript(self, session_id: str) -> str:
        """Assemble full transcript from chunks, in order."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT text FROM transcript_chunks "
                "WHERE session_id = ? ORDER BY chunk_index",
                (session_id,),
            ).fetchall()
            return " ".join(r["text"] for r in rows if r["text"].strip())

    def chunk_exists(self, session_id: str, chunk_index: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM transcript_chunks WHERE session_id = ? AND chunk_index = ?",
                (session_id, chunk_index),
            ).fetchone()
            return row is not None

    def search_transcripts(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across all transcript chunks."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT tc.session_id, tc.chunk_index, tc.text,
                          tc.started_at_offset, s.title, s.started_at
                   FROM transcript_chunks tc
                   JOIN sessions s ON s.id = tc.session_id
                   WHERE tc.text LIKE ?
                   ORDER BY s.started_at DESC
                   LIMIT ?""",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Action items
    # ------------------------------------------------------------------

    def save_action(self, action: ActionItem) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO action_items
                   (id, session_id, task, assignee, deadline_raw, deadline_parsed,
                    context, source_quote, confidence, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action.id,
                    action.session_id,
                    action.task,
                    action.assignee,
                    action.deadline_raw,
                    action.deadline_parsed,
                    action.context,
                    action.source_quote,
                    action.confidence,
                    action.status.value,
                ),
            )

    def update_action_status(self, action_id: str, status: ActionStatus) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE action_items SET status = ? WHERE id = ?",
                (status.value, action_id),
            )

    def get_actions(self, session_id: str) -> list[ActionItem]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM action_items WHERE session_id = ? ORDER BY confidence DESC",
                (session_id,),
            ).fetchall()
            return [_row_to_action(r) for r in rows]

    # ------------------------------------------------------------------
    # Cleanup (auto-delete old audio)
    # ------------------------------------------------------------------

    def cleanup_old_audio(self, max_age_days: int) -> int:
        """
        Delete audio chunk files for sessions older than max_age_days.
        Keeps transcripts and Word docs (text is tiny; audio is large).
        Returns number of sessions cleaned.
        """
        if max_age_days <= 0:
            return 0

        import os
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, audio_dir FROM sessions WHERE started_at < ? AND audio_dir != ''",
                (cutoff,),
            ).fetchall()

        cleaned = 0
        for row in rows:
            audio_dir = Path(row["audio_dir"])
            if audio_dir.exists():
                wav_files = list(audio_dir.glob("chunk_*.wav"))
                for wav in wav_files:
                    try:
                        wav.unlink()
                    except OSError as e:
                        logger.warning(f"Could not delete {wav}: {e}")
                if wav_files:
                    cleaned += 1
                    logger.info(
                        f"Cleaned {len(wav_files)} audio files from session {row['id']}"
                    )

        return cleaned


# ------------------------------------------------------------------
# Row → model converters
# ------------------------------------------------------------------

def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"] or "",
        status=SessionStatus(row["status"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        stopped_at=datetime.fromisoformat(row["stopped_at"]) if row["stopped_at"] else None,
        audio_dir=row["audio_dir"] or "",
        transcript_path=row["transcript_path"],
        notes_path=row["notes_path"],
        model_whisper=row["model_whisper"] or "",
        model_llm=row["model_llm"] or "",
    )


def _row_to_action(row: sqlite3.Row) -> ActionItem:
    return ActionItem(
        id=row["id"],
        session_id=row["session_id"],
        task=row["task"],
        assignee=row["assignee"],
        deadline_raw=row["deadline_raw"],
        deadline_parsed=row["deadline_parsed"],
        context=row["context"],
        source_quote=row["source_quote"],
        confidence=row["confidence"],
        reminder_id=row["reminder_id"],
        status=ActionStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
