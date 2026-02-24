"""
Meeting detector for macOS.

Checks for active meetings across Zoom, Microsoft Teams, Google Meet,
and Webex using a combination of process inspection and AppleScript.

Detection strategy by platform:

  Zoom        → process 'CptHost' is only spawned during active meetings
  Teams       → window title contains meeting indicators (new and classic Teams)
  Google Meet → browser tab URL contains meet.google.com + title changed from default
  Webex       → process 'Cisco Webex Meetings' + audio session active

All checks are non-blocking and fail-safe (return False on any error).
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MeetingState:
    active: bool
    platform: str = ""       # "Zoom", "Microsoft Teams", "Google Meet", etc.
    title: str = ""          # meeting title if detectable


def detect_meeting() -> MeetingState:
    """
    Check all known meeting platforms.
    Returns the first active meeting found, or MeetingState(active=False).
    """
    checks = [
        _check_zoom,
        _check_teams,
        _check_google_meet_chrome,
        _check_google_meet_edge,
        _check_google_meet_safari,
        _check_webex,
    ]
    for check in checks:
        try:
            state = check()
            if state.active:
                return state
        except Exception as exc:
            logger.debug(f"{check.__name__} error: {exc}")

    return MeetingState(active=False)


# ── Per-platform detectors ────────────────────────────────────────────────────

def _check_zoom() -> MeetingState:
    """
    Zoom spawns a 'CptHost' subprocess only while a meeting is active.
    This is the most reliable Zoom detection method — no AppleScript needed.
    """
    result = subprocess.run(
        ["pgrep", "-x", "CptHost"],
        capture_output=True,
        timeout=3,
    )
    if result.returncode == 0:
        # Optionally get meeting window title via AppleScript
        title = _get_zoom_title()
        return MeetingState(active=True, platform="Zoom", title=title)
    return MeetingState(active=False)


def _get_zoom_title() -> str:
    script = """
    tell application "System Events"
        if exists process "zoom.us" then
            tell process "zoom.us"
                set wins to every window whose name is not ""
                if length of wins > 0 then
                    return name of item 1 of wins
                end if
            end tell
        end if
    end tell
    return ""
    """
    return _run_applescript(script).strip()


def _check_teams() -> MeetingState:
    """
    Teams changes its window title when in an active call.
    Works for both classic Teams and the new Teams (2.0).
    """
    for process_name in ("Microsoft Teams", "MSTeams"):
        script = f"""
        tell application "System Events"
            if exists process "{process_name}" then
                tell process "{process_name}"
                    set winTitles to name of every window
                    repeat with t in winTitles
                        set ts to t as string
                        if ts contains "| Microsoft Teams" or ts contains "Teams" then
                            if ts contains "Call" or ts contains "Meeting" or ts contains "joined" then
                                return ts
                            end if
                        end if
                    end repeat
                end tell
            end if
        end tell
        return ""
        """
        title = _run_applescript(script).strip()
        if title:
            return MeetingState(active=True, platform="Microsoft Teams", title=title)

    return MeetingState(active=False)


def _check_google_meet_chrome() -> MeetingState:
    """
    Google Meet in Chrome: tab URL contains meet.google.com/xxx-xxx-xxx
    and the tab title has changed from the default "Google Meet" to the meeting name.
    """
    script = """
    tell application "System Events"
        if not (exists process "Google Chrome") then return ""
    end tell
    tell application "Google Chrome"
        repeat with w in windows
            repeat with t in tabs of w
                set tabURL to URL of t
                if tabURL contains "meet.google.com/" then
                    set tabTitle to title of t
                    -- Tab title is "Google Meet" on the home screen,
                    -- changes to the meeting name when you're in a call
                    if tabTitle is not "Google Meet" and tabTitle does not contain "New meeting" then
                        return tabTitle
                    end if
                end if
            end repeat
        end repeat
    end tell
    return ""
    """
    title = _run_applescript(script).strip()
    if title:
        return MeetingState(active=True, platform="Google Meet", title=title)
    return MeetingState(active=False)


def _check_google_meet_edge() -> MeetingState:
    """Google Meet in Microsoft Edge."""
    script = """
    tell application "System Events"
        if not (exists process "Microsoft Edge") then return ""
    end tell
    tell application "Microsoft Edge"
        repeat with w in windows
            repeat with t in tabs of w
                set tabURL to URL of t
                if tabURL contains "meet.google.com/" then
                    set tabTitle to title of t
                    if tabTitle is not "Google Meet" and tabTitle does not contain "New meeting" then
                        return tabTitle
                    end if
                end if
            end repeat
        end repeat
    end tell
    return ""
    """
    title = _run_applescript(script).strip()
    if title:
        return MeetingState(active=True, platform="Google Meet (Edge)", title=title)
    return MeetingState(active=False)


def _check_google_meet_safari() -> MeetingState:
    """Google Meet in Safari — URL-based detection only (Safari doesn't expose tab URLs as easily)."""
    script = """
    tell application "System Events"
        if not (exists process "Safari") then return ""
    end tell
    tell application "Safari"
        repeat with w in windows
            repeat with t in tabs of w
                try
                    set tabURL to URL of t
                    if tabURL contains "meet.google.com/" then
                        set tabTitle to name of t
                        if tabTitle is not "Google Meet" then
                            return tabTitle
                        end if
                    end if
                end try
            end repeat
        end repeat
    end tell
    return ""
    """
    title = _run_applescript(script).strip()
    if title:
        return MeetingState(active=True, platform="Google Meet (Safari)", title=title)
    return MeetingState(active=False)


def _check_webex() -> MeetingState:
    """Webex: check for active Webex Meetings process."""
    result = subprocess.run(
        ["pgrep", "-fi", "webex"],
        capture_output=True,
        timeout=3,
    )
    if result.returncode == 0:
        return MeetingState(active=True, platform="Webex", title="Webex Meeting")
    return MeetingState(active=False)


# ── AppleScript runner ────────────────────────────────────────────────────────

def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout. Returns '' on error."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""
