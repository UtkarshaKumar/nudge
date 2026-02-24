"""
macOS Reminders integration via AppleScript.

Adds action items directly to the Reminders app.
Parses natural language deadlines into actual dates.
No UI shown — completely silent.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── AppleScript helpers ───────────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Escape a string for safe embedding in AppleScript."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _run_script(script: str) -> bool:
    """Execute an AppleScript snippet. Returns True on success."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        logger.warning(f"AppleScript error: {result.stderr.strip()[:200]}")
        return False
    return True


# ── Deadline parsing ──────────────────────────────────────────────────────────

def parse_deadline(deadline_str: Optional[str]) -> Optional[str]:
    """
    Convert natural language deadline to AppleScript date string.
    Returns None if the deadline can't be parsed.

    Examples:
        "today"        → "February 24, 2026 at 06:00 PM"
        "tomorrow"     → "February 25, 2026 at 09:00 AM"
        "end of week"  → next Friday at 5 PM
        "ASAP"         → 2 business days from now at 9 AM
        "next week"    → 7 days from now at 9 AM
    """
    if not deadline_str:
        return None

    now = datetime.now()
    dl = deadline_str.lower().strip()
    target: Optional[datetime] = None

    if "today" in dl or "eod" in dl or "end of day" in dl:
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
    elif "tomorrow" in dl:
        target = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif "end of week" in dl or "friday" in dl:
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        target = (now + timedelta(days=days_until_friday)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
    elif "next week" in dl:
        target = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif "asap" in dl or "as soon as possible" in dl or "urgent" in dl:
        # 2 business days
        delta = 3 if now.weekday() >= 4 else 2  # skip weekend
        target = (now + timedelta(days=delta)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif "end of month" in dl:
        if now.month == 12:
            target = datetime(now.year + 1, 1, 1) - timedelta(days=1)
        else:
            target = datetime(now.year, now.month + 1, 1) - timedelta(days=1)
        target = target.replace(hour=17, minute=0, second=0, microsecond=0)
    elif "next month" in dl:
        if now.month == 12:
            target = datetime(now.year + 1, 1, now.day, 9, 0, 0)
        else:
            target = datetime(now.year, now.month + 1, now.day, 9, 0, 0)

    if target:
        return target.strftime("%B %d, %Y at %I:%M %p")
    return None


# ── List management ───────────────────────────────────────────────────────────

def ensure_list(list_name: str) -> None:
    """Create a Reminders list if it doesn't already exist."""
    script = f'''tell application "Reminders"
    if not (exists list "{_escape(list_name)}") then
        make new list with properties {{name:"{_escape(list_name)}"}}
    end if
end tell'''
    _run_script(script)


# ── Reminder creation ─────────────────────────────────────────────────────────

def add_reminder(
    task: str,
    deadline: Optional[str] = None,
    notes: Optional[str] = None,
    list_name: str = "Meeting Actions",
) -> bool:
    """Create a single Reminder. Returns True on success."""
    task_esc = _escape(task)
    date_str = parse_deadline(deadline)

    props = f'name:"{task_esc}"'
    if date_str:
        props += f', due date:date "{_escape(date_str)}"'
    if notes:
        props += f', body:"{_escape(notes)}"'

    script = f'''tell application "Reminders"
    set theList to list "{_escape(list_name)}"
    make new reminder at end of theList with properties {{{props}}}
end tell'''
    return _run_script(script)


def add_actions_to_reminders(
    actions: list[dict],
    session_title: str,
    list_name: str = "Meeting Actions",
    min_confidence: float = 0.6,
    include_context: bool = True,
    include_source_quote: bool = True,
) -> tuple[int, int]:
    """
    Add qualifying action items to macOS Reminders.
    Returns (added_count, skipped_count).
    """
    ensure_list(list_name)
    added = 0
    skipped = 0

    for action in actions:
        confidence = action.get("confidence", 0)
        if confidence < min_confidence:
            skipped += 1
            continue

        task = action.get("task", "").strip()
        if not task:
            skipped += 1
            continue

        # Prepend assignee to task for clarity
        assignee = action.get("assignee")
        if assignee:
            task = f"[{assignee}] {task}"

        # Build notes body
        notes_parts = [f"Meeting: {session_title}"]
        if include_context and action.get("context"):
            notes_parts.append(action["context"])
        if include_source_quote and action.get("source_quote"):
            notes_parts.append(f'"{action["source_quote"]}"')
        notes = "\n\n".join(notes_parts)

        success = add_reminder(
            task=task,
            deadline=action.get("deadline"),
            notes=notes,
            list_name=list_name,
        )
        if success:
            added += 1
            logger.debug(f"Added reminder: {task[:60]}")
        else:
            skipped += 1

    return added, skipped
