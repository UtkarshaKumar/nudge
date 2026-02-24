"""
nudge CLI — all commands.

Commands:
  start         Start recording (blocks until Ctrl+C)
  list          List recent sessions
  process       Re-process a session (re-extract actions + notes)
  recover       Process any incomplete sessions from previous runs
  search        Search across all meeting transcripts
  digest        Generate a weekly summary of all meetings
  cleanup       Delete old audio files to free disk space
  doctor        Diagnose setup issues
  config        Show or set configuration values
  watch run     Run the auto-detect watcher (blocks forever)
  watch install Install watcher as a macOS login item (LaunchAgent)
  watch uninstall Remove the login item
  watch status  Show whether the watcher is running
  watch logs    Tail the watcher log
"""
from __future__ import annotations

import json
import logging
import queue
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ..audio.capture import AudioCapture
from ..audio.devices import check_disk_space, is_blackhole_available
from ..config import CONFIG_FILE, load_config
from ..extraction.ollama_client import ActionExtractor, DigestGenerator, MeetingAnalyzer
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
from . import display

app = typer.Typer(
    name="nudge",
    help="Your silent meeting scribe.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console()
logger = logging.getLogger(__name__)

# Lockfile prevents double-recording
_LOCK_FILE = Path.home() / ".nudge" / "nudge.lock"


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _get_db(config=None) -> Database:
    cfg = config or load_config()
    return Database(cfg.storage.db_path)


# ── start ─────────────────────────────────────────────────────────────────────

@app.command()
def start(
    title: Optional[str] = typer.Option(
        None, "--title", "-t", help="Session title (e.g. 'Daily standup')"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress live transcript"),
) -> None:
    """Start capturing meeting audio. Press Ctrl+C to stop."""
    config = load_config()
    _setup_logging(config.display.log_level)

    display.print_banner()

    # ── Pre-flight checks ─────────────────────────────────────────────
    if _LOCK_FILE.exists():
        display.print_error(
            "Another nudge session appears to be running.\n"
            f"If that's wrong, delete {_LOCK_FILE} and try again."
        )
        raise typer.Exit(1)

    if not is_blackhole_available(config.audio.device):
        display.print_error(
            f"Audio device '{config.audio.device}' not found.\n"
            "Run: nudge doctor"
        )
        raise typer.Exit(1)

    ok, avail_gb = check_disk_space(config.sessions_path, required_gb=2.0)
    if not ok:
        display.print_warn(f"Low disk space: {avail_gb:.1f} GB available. Recording may fail.")

    # ── Auto-cleanup old audio ─────────────────────────────────────────
    db = _get_db(config)
    if config.storage.auto_delete_audio_days > 0:
        cleaned = db.cleanup_old_audio(config.storage.auto_delete_audio_days)
        if cleaned:
            display.print_info(f"Cleaned audio from {cleaned} old session(s)")

    # ── Warn about incomplete sessions ─────────────────────────────────
    incomplete = db.get_incomplete_sessions()
    if incomplete:
        display.print_warn(
            f"{len(incomplete)} unprocessed session(s) found. "
            "Run 'nudge recover' after this meeting."
        )

    # ── Create session ────────────────────────────────────────────────
    session_title = title or f"Meeting {datetime.now().strftime('%b %d %H:%M')}"
    session = Session(
        title=session_title,
        model_whisper=config.whisper.model,
        model_llm=config.ollama.model,
    )
    sessions_dir = config.sessions_path / session.id
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session.audio_dir = str(sessions_dir)
    db.create_session(session)

    # ── Lock ──────────────────────────────────────────────────────────
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.write_text(session.id)

    # ── Load Whisper ──────────────────────────────────────────────────
    engine = WhisperEngine(config.whisper)
    console.print("[dim]Loading Whisper model...[/dim]", end="")
    engine.load()
    console.print(" [green]ready[/green]")

    # ── Transcription queue & worker ──────────────────────────────────
    transcription_queue: queue.Queue[Optional[Path]] = queue.Queue()
    transcript_offset = 0.0

    def transcription_worker() -> None:
        nonlocal transcript_offset
        while True:
            chunk_path = transcription_queue.get()
            if chunk_path is None:
                break
            try:
                index = int(chunk_path.stem.split("_")[1])
                result = engine.transcribe(chunk_path, chunk_index=index)

                if not result.is_empty:
                    db.save_chunk(
                        TranscriptChunkModel(
                            session_id=session.id,
                            chunk_index=result.chunk_index,
                            audio_file=str(chunk_path),
                            text=result.text,
                            started_at_offset=transcript_offset,
                            ended_at_offset=transcript_offset + config.audio.chunk_duration,
                            language=result.language,
                            confidence=result.language_probability,
                        )
                    )
                    if not quiet and config.display.live_transcript:
                        for seg in result.segments:
                            display.print_transcript_line(
                                transcript_offset + seg.start, seg.text
                            )
            except Exception as exc:
                logger.error(f"Transcription error on {chunk_path.name}: {exc}")
            finally:
                transcript_offset += config.audio.chunk_duration

    import threading
    worker_thread = threading.Thread(
        target=transcription_worker,
        daemon=True,
        name="nudge-transcribe",
    )
    worker_thread.start()

    # ── Audio capture ─────────────────────────────────────────────────
    capture = AudioCapture(config.audio, sessions_dir)
    capture.on_chunk_saved(lambda path: transcription_queue.put(path))

    # ── Shutdown handler ──────────────────────────────────────────────
    def _shutdown(signum=None, frame=None) -> None:
        display.print_info("\nStopping capture...")
        capture.stop()

        # Signal transcription worker to finish
        transcription_queue.put(None)
        worker_thread.join(timeout=120)  # wait up to 2 min for last chunk

        db.update_session_status(
            session.id,
            SessionStatus.STOPPED,
            stopped_at=datetime.now(),
        )
        _LOCK_FILE.unlink(missing_ok=True)
        _process_session(session.id, config, db)
        raise typer.Exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    # ── Start ─────────────────────────────────────────────────────────
    display.print_recording_status(config.audio.device, session_title)

    try:
        capture.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        _shutdown()
    except Exception as exc:
        logger.error(f"Fatal error: {exc}")
        _LOCK_FILE.unlink(missing_ok=True)
        db.update_session_status(session.id, SessionStatus.ERROR)
        raise


# ── list ──────────────────────────────────────────────────────────────────────

@app.command(name="list")
def list_sessions(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of sessions to show"),
) -> None:
    """List recent recording sessions."""
    db = _get_db()
    sessions = db.list_sessions(limit=limit)
    display.print_session_list(sessions)


# ── process ───────────────────────────────────────────────────────────────────

@app.command()
def process(
    session_id: Optional[str] = typer.Argument(
        None, help="Session ID to process (default: most recent)"
    ),
) -> None:
    """Re-extract action items and regenerate meeting notes for a session."""
    config = load_config()
    db = _get_db(config)

    if session_id is None:
        sessions = db.list_sessions(limit=1)
        if not sessions:
            display.print_error("No sessions found.")
            raise typer.Exit(1)
        session_id = sessions[0].id

    session = db.get_session(session_id)
    if not session:
        display.print_error(f"Session '{session_id}' not found.")
        raise typer.Exit(1)

    _process_session(session_id, config, db)


# ── recover ───────────────────────────────────────────────────────────────────

@app.command()
def recover() -> None:
    """Process any incomplete sessions left by crashed or interrupted runs."""
    config = load_config()
    db = _get_db(config)

    incomplete = db.get_incomplete_sessions()
    if not incomplete:
        display.print_success("No incomplete sessions found.")
        return

    display.print_warn(f"Found {len(incomplete)} incomplete session(s).")
    for session in incomplete:
        console.print(f"\n[dim]→[/dim] {session.id}  {session.title}")
        _process_session(session.id, config, db)


# ── search ────────────────────────────────────────────────────────────────────

@app.command()
def search(
    query: str = typer.Argument(..., help="Text to search for in transcripts"),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Search across all meeting transcripts."""
    db = _get_db()
    results = db.search_transcripts(query, limit=limit)
    display.print_search_results(results, query)


# ── digest ────────────────────────────────────────────────────────────────────

@app.command()
def digest(
    days: int = typer.Option(7, "--days", "-d", help="Look back N days (default: 7)"),
) -> None:
    """Generate a summary digest of recent meetings."""
    config = load_config()
    db = _get_db(config)

    end = datetime.now()
    start = end - timedelta(days=days)
    sessions = db.get_sessions_in_range(start, end)

    complete_sessions = [s for s in sessions if s.status == SessionStatus.COMPLETE]
    if not complete_sessions:
        display.print_info(f"No completed sessions in the last {days} days.")
        return

    period = f"{start.strftime('%b %d')} – {end.strftime('%b %d %Y')}"
    console.print(f"\n[dim]Analyzing {len(complete_sessions)} meeting(s)...[/dim]")

    summaries = []
    for s in complete_sessions:
        transcript = db.get_transcript(s.id)
        if transcript.strip():
            analyzer = MeetingAnalyzer(config.ollama)
            analysis = analyzer.analyze(transcript)
            actions = db.get_actions(s.id)
            action_list = ", ".join(a.task for a in actions[:5])
            summaries.append(
                f"Meeting: {s.title} ({s.started_at.strftime('%b %d')})\n"
                f"Summary: {analysis.get('summary', '')}\n"
                f"Actions: {action_list}"
            )

    gen = DigestGenerator(config.ollama)
    result = gen.generate(summaries)
    display.print_digest(result, period)


# ── cleanup ───────────────────────────────────────────────────────────────────

@app.command()
def cleanup(
    days: int = typer.Option(
        None,
        "--days",
        help="Delete audio older than N days (default: from config)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
) -> None:
    """Delete old audio recordings to free disk space."""
    config = load_config()
    max_days = days if days is not None else config.storage.auto_delete_audio_days

    if max_days <= 0:
        display.print_info("auto_delete_audio_days is 0 — no cleanup configured.")
        return

    db = _get_db(config)

    if dry_run:
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=max_days)
        sessions = db.list_sessions(limit=500)
        old = [
            s for s in sessions
            if s.started_at < cutoff and Path(s.audio_dir).exists()
        ]
        if not old:
            display.print_success(f"Nothing to clean up (threshold: {max_days} days).")
        else:
            console.print(f"Would clean audio from {len(old)} session(s):")
            for s in old:
                wav_count = len(list(Path(s.audio_dir).glob("chunk_*.wav")))
                console.print(f"  {s.id}  {s.title}  ({wav_count} files)")
        return

    cleaned = db.cleanup_old_audio(max_days)
    if cleaned:
        display.print_success(f"Cleaned audio from {cleaned} session(s) older than {max_days} days.")
    else:
        display.print_success("Nothing to clean up.")


# ── doctor ────────────────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """Diagnose nudge setup — check all dependencies."""
    config = load_config()
    console.print("\n[bold]nudge doctor[/bold]\n")
    all_ok = True

    # BlackHole
    bh_ok = is_blackhole_available(config.audio.device)
    display.print_check(
        f"BlackHole audio device ({config.audio.device})",
        bh_ok,
        "not found — run: brew install blackhole-2ch" if not bh_ok else "",
    )
    all_ok = all_ok and bh_ok

    # Ollama
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        ollama_ok = result.returncode == 0
        display.print_check("Ollama installed", ollama_ok)
        if ollama_ok:
            model_ok = config.ollama.model in result.stdout
            display.print_check(
                f"Model {config.ollama.model}",
                model_ok,
                f"not found — run: ollama pull {config.ollama.model}" if not model_ok else "",
            )
            all_ok = all_ok and model_ok
        else:
            all_ok = False
    except FileNotFoundError:
        display.print_check("Ollama installed", False, "run: brew install ollama")
        all_ok = False

    # faster-whisper
    try:
        import faster_whisper  # noqa: F401
        display.print_check("faster-whisper installed", True)
    except ImportError:
        display.print_check("faster-whisper installed", False, "run: pip install faster-whisper")
        all_ok = False

    # python-docx
    try:
        import docx  # noqa: F401
        display.print_check("python-docx installed", True)
    except ImportError:
        display.print_check("python-docx installed", False, "run: pip install python-docx")
        all_ok = False

    # Disk space
    _, avail_gb = check_disk_space(config.sessions_path, required_gb=2.0)
    disk_ok = avail_gb >= 2.0
    display.print_check(
        f"Disk space ({avail_gb:.1f} GB available)",
        disk_ok,
        "low — recordings may fail" if not disk_ok else "",
    )

    # Config
    config_exists = CONFIG_FILE.exists()
    display.print_check(
        f"Config file ({CONFIG_FILE})",
        config_exists,
        "using defaults — run install.sh to create" if not config_exists else "",
    )

    # Reminders permission check (non-blocking)
    console.print()
    if all_ok:
        display.print_success("All checks passed. Run 'nudge start' to begin recording.")
    else:
        display.print_error("Some checks failed. Fix issues above, then re-run 'nudge doctor'.")


# ── config ────────────────────────────────────────────────────────────────────

config_app = typer.Typer(name="config", help="View or edit configuration.", no_args_is_help=True)
app.add_typer(config_app)


@config_app.command("show")
def config_show() -> None:
    """Print current configuration."""
    config = load_config()
    import yaml
    console.print(yaml.dump(config.model_dump(), default_flow_style=False))


@config_app.command("path")
def config_path() -> None:
    """Show path to the config file."""
    console.print(str(CONFIG_FILE))


@config_app.command("edit")
def config_edit() -> None:
    """Open config file in $EDITOR (creates it if missing)."""
    import os
    import shutil

    if not CONFIG_FILE.exists():
        # Copy template
        template = Path(__file__).parent.parent.parent / "config.yaml.template"
        if template.exists():
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(template, CONFIG_FILE)

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(CONFIG_FILE)])


# ── Core processing logic (shared by start, process, recover) ─────────────────

def _process_session(session_id: str, config, db: Database) -> None:
    """
    Post-meeting pipeline:
      1. Transcribe any remaining chunks
      2. Extract action items via Ollama
      3. Analyze meeting (summary, decisions)
      4. Add to macOS Reminders
      5. Generate Word document
    """
    session = db.get_session(session_id)
    if not session:
        display.print_error(f"Session {session_id} not found.")
        return

    display.print_processing_header(session.title)
    db.update_session_status(session_id, SessionStatus.PROCESSING)

    sessions_dir = Path(session.audio_dir)
    all_chunks = sorted(sessions_dir.glob("chunk_*.wav"))

    with display.make_progress() as progress:
        # ── Step 1: Transcribe remaining chunks ───────────────────────
        transcribe_task = progress.add_task(
            "Transcribing...", total=max(len(all_chunks), 1)
        )
        engine = WhisperEngine(config.whisper)
        new_chunks = 0

        for chunk_path in all_chunks:
            index = int(chunk_path.stem.split("_")[1])
            if not db.chunk_exists(session_id, index):
                engine.load()
                result = engine.transcribe(chunk_path, chunk_index=index)
                if not result.is_empty:
                    db.save_chunk(
                        TranscriptChunkModel(
                            session_id=session_id,
                            chunk_index=result.chunk_index,
                            audio_file=str(chunk_path),
                            text=result.text,
                            language=result.language,
                            confidence=result.language_probability,
                        )
                    )
                new_chunks += 1
            progress.advance(transcribe_task)

        full_transcript = db.get_transcript(session_id)
        if not full_transcript.strip():
            display.print_warn("No speech detected in this recording.")
            db.update_session_status(session_id, SessionStatus.COMPLETE)
            return

        # Save plain text transcript
        transcript_path = sessions_dir / "transcript.txt"
        transcript_path.write_text(full_transcript)
        db.update_session_status(
            session_id, SessionStatus.PROCESSING, transcript_path=str(transcript_path)
        )

        # ── Step 2: Extract action items ──────────────────────────────
        extract_task = progress.add_task("Extracting action items...", total=1)
        extractor = ActionExtractor(config.ollama)
        raw_actions = extractor.extract(full_transcript)

        for raw in raw_actions:
            task = raw.get("task", "").strip()
            if task:
                action = ActionItem(
                    session_id=session_id,
                    task=task,
                    assignee=raw.get("assignee"),
                    deadline_raw=raw.get("deadline"),
                    context=raw.get("context"),
                    source_quote=raw.get("source_quote"),
                    confidence=raw.get("confidence", 0),
                )
                db.save_action(action)

        (sessions_dir / "actions.json").write_text(
            json.dumps(raw_actions, indent=2, ensure_ascii=False)
        )
        progress.advance(extract_task)

        # ── Step 3: Analyze meeting ───────────────────────────────────
        analyze_task = progress.add_task("Analyzing meeting...", total=1)
        analyzer = MeetingAnalyzer(config.ollama)
        analysis = analyzer.analyze(full_transcript)

        # Use AI title if user didn't provide one, or title looks auto-generated
        if analysis.get("title") and "Meeting " in session.title:
            session.title = analysis["title"]

        progress.advance(analyze_task)

        # ── Step 4: Add to Reminders ──────────────────────────────────
        reminders_task = progress.add_task("Adding to Reminders...", total=1)
        added, skipped = add_actions_to_reminders(
            actions=raw_actions,
            session_title=session.title,
            list_name=config.reminders.list_name,
            min_confidence=config.reminders.min_confidence,
            include_context=config.reminders.include_context,
            include_source_quote=config.reminders.include_source_quote,
        )
        for a in db.get_actions(session_id):
            db.update_action_status(
                a.id, ActionStatus.ADDED if added > 0 else ActionStatus.SKIPPED
            )
        progress.advance(reminders_task)

        # ── Step 5: Generate Word document ────────────────────────────
        notes_task = progress.add_task("Writing meeting notes...", total=1)
        notes_path = generate_meeting_notes(
            title=session.title,
            started_at=session.started_at,
            duration_seconds=session.duration_seconds,
            summary=analysis.get("summary", ""),
            decisions=analysis.get("decisions", []),
            actions=raw_actions,
            transcript=_format_transcript_for_doc(
                full_transcript,
                session.started_at,
            ),
            participants=analysis.get("participants", []),
            config=config.notes,
            session_id=session_id,
        )
        progress.advance(notes_task)

    db.update_session_status(
        session_id,
        SessionStatus.COMPLETE,
        stopped_at=session.stopped_at or datetime.now(),
        notes_path=str(notes_path) if notes_path else None,
    )

    display.print_action_summary(
        raw_actions,
        added,
        skipped,
        config.reminders.list_name,
        notes_path=str(notes_path) if notes_path else None,
    )

    # Auto-open Word doc
    if notes_path:
        open_notes(notes_path)


def _format_transcript_for_doc(transcript: str, started_at: datetime) -> str:
    """Wrap transcript in a basic format for the Word document."""
    lines = []
    words = transcript.split()
    # Group into ~15-word lines for readability
    for i in range(0, len(words), 15):
        lines.append(" ".join(words[i:i + 15]))
    return "\n".join(lines)


# ── watch ─────────────────────────────────────────────────────────────────────

_PLIST_LABEL = "com.nudge.watcher"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"
_WATCHER_LOG = Path.home() / ".nudge" / "watcher.log"
_APP_BUNDLE = Path.home() / "Applications" / "nudge.app"
_APP_EXECUTABLE = _APP_BUNDLE / "Contents" / "MacOS" / "nudge-watcher"

watch_app = typer.Typer(
    name="watch",
    help="Auto-detect meetings and record automatically.",
    no_args_is_help=True,
)
app.add_typer(watch_app)


@watch_app.command("run")
def watch_run() -> None:
    """
    Run the meeting watcher (blocks forever).
    Automatically starts recording when a call is detected,
    stops and processes when the call ends.

    Normally you don't call this directly — use 'nudge watch install'
    to run it automatically at login.
    """
    config = load_config()
    _setup_logging(config.display.log_level)

    from ..watcher.watcher import MeetingWatcher
    watcher = MeetingWatcher(config)
    watcher.run()


def _generate_icon(resources_dir: Path) -> None:
    """Generate nudge.icns using pure stdlib PNG + sips + iconutil."""
    import struct, zlib, math, tempfile, shutil

    def make_png(path: str, size: int = 512) -> None:
        cx = cy = size // 2
        r = size // 2 - 4
        rows = []
        for y in range(size):
            row = bytearray()
            for x in range(size):
                dx, dy = x - cx, y - cy
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > r:
                    row += b'\x00\x00\x00\x00'
                    continue
                t = dist / r
                bg = (int(38 + 22*t), int(24 + 18*t), int(92 + 28*t), 255)
                bar_w = max(int(size * 0.05), 1)
                gap   = int(size * 0.12)
                bh    = [int(size*0.32), int(size*0.52), int(size*0.32)]
                bx    = [cx - gap, cx, cx + gap]
                in_bar = False
                for i in range(3):
                    if abs(x - bx[i]) <= bar_w:
                        top, bot = cy - bh[i]//2, cy + bh[i]//2
                        if top <= y <= bot:
                            in_bar = True
                            break
                row += bytes((255, 255, 255, 255) if in_bar else bg)
            rows.append(bytes(row))

        def chunk(t: bytes, d: bytes) -> bytes:
            c = t + d
            return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

        raw = b''.join(b'\x00' + row for row in rows)
        with open(path, 'wb') as f:
            f.write(
                b'\x89PNG\r\n\x1a\n' +
                chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 6)) +
                chunk(b'IEND', b'')
            )

    with tempfile.TemporaryDirectory() as tmp:
        src_png = str(Path(tmp) / "nudge_512.png")
        iconset_dir = str(Path(tmp) / "nudge.iconset")
        Path(iconset_dir).mkdir()
        make_png(src_png)
        for s in [16, 32, 64, 128, 256, 512]:
            subprocess.run(
                ["sips", "-z", str(s), str(s), src_png, "--out", f"{iconset_dir}/icon_{s}x{s}.png"],
                capture_output=True,
            )
            subprocess.run(
                ["sips", "-z", str(s*2), str(s*2), src_png, "--out", f"{iconset_dir}/icon_{s}x{s}@2x.png"],
                capture_output=True,
            )
        resources_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", iconset_dir, "-o", str(resources_dir / "nudge.icns")],
            capture_output=True,
        )


def _build_app_bundle(python_bin: Path, nudge_script: Path) -> None:
    """Create ~/Applications/nudge.app bundle with icon and ad-hoc sign it."""
    macos_dir = _APP_BUNDLE / "Contents" / "MacOS"
    resources_dir = _APP_BUNDLE / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)

    # Generate icon
    _generate_icon(resources_dir)

    # Info.plist — gives Login Items the display name "nudge"
    info_plist = _APP_BUNDLE / "Contents" / "Info.plist"
    info_plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>CFBundleName</key>\n'
        '    <string>nudge</string>\n'
        '    <key>CFBundleDisplayName</key>\n'
        '    <string>nudge</string>\n'
        '    <key>CFBundleIdentifier</key>\n'
        '    <string>com.nudge.watcher</string>\n'
        '    <key>CFBundleVersion</key>\n'
        '    <string>1.0</string>\n'
        '    <key>CFBundleShortVersionString</key>\n'
        '    <string>1.0</string>\n'
        '    <key>CFBundleExecutable</key>\n'
        '    <string>nudge-watcher</string>\n'
        '    <key>CFBundleIconFile</key>\n'
        '    <string>nudge</string>\n'
        '    <key>LSUIElement</key>\n'
        '    <true/>\n'
        '</dict>\n'
        '</plist>\n'
    )

    # Launcher executable — thin shell script that runs the Python watcher
    py = str(python_bin)
    ns = str(nudge_script)
    _APP_EXECUTABLE.write_text(f"#!/bin/zsh\nexec '{py}' '{ns}' watch run\n")
    _APP_EXECUTABLE.chmod(0o755)

    # Ad-hoc sign so macOS allows execution on Apple Silicon
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(_APP_BUNDLE)],
        capture_output=True,
    )


@watch_app.command("install")
def watch_install() -> None:
    """
    Install nudge as a macOS login item.
    After this, nudge watches for meetings automatically every time you log in.
    """
    python_bin = Path(sys.executable)
    nudge_script = Path(__file__).parent.parent.parent / "nudge.py"

    if not python_bin.exists():
        display.print_error(f"Python not found at {python_bin}")
        raise typer.Exit(1)

    # Build ~/Applications/nudge.app so Login Items shows "nudge" not "python"
    _build_app_bundle(python_bin, nudge_script)
    display.print_success(f"App bundle created: {_APP_BUNDLE}")

    # Load plist template
    template_path = nudge_script.parent / "com.nudge.watcher.plist.template"
    if not template_path.exists():
        display.print_error(f"Plist template not found: {template_path}")
        raise typer.Exit(1)

    plist_content = template_path.read_text()
    plist_content = plist_content.replace("APP_EXECUTABLE_PATH", str(_APP_EXECUTABLE))
    plist_content = plist_content.replace("NUDGE_HOME", str(Path.home() / ".nudge"))
    plist_content = plist_content.replace("HOME_PATH", str(Path.home()))

    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(plist_content)
    display.print_success(f"Plist written: {_PLIST_PATH}")

    # Load the agent
    result = subprocess.run(
        ["launchctl", "load", "-w", str(_PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        display.print_success("nudge watcher installed as login item")
        console.print()
        console.print("  nudge will now automatically start recording when you join any meeting.")
        console.print("  To stop it:  [bold]nudge watch uninstall[/bold]")
        console.print(f"  Logs at:     [dim]{_WATCHER_LOG}[/dim]")
    else:
        display.print_error(f"launchctl load failed: {result.stderr.strip()}")
        console.print(f"  Try manually: launchctl load -w {_PLIST_PATH}")


@watch_app.command("uninstall")
def watch_uninstall() -> None:
    """Remove the nudge login item. You can still run 'nudge watch run' manually."""
    if not _PLIST_PATH.exists():
        display.print_info("Watcher login item not installed.")
        return

    subprocess.run(
        ["launchctl", "unload", "-w", str(_PLIST_PATH)],
        capture_output=True,
    )
    _PLIST_PATH.unlink(missing_ok=True)

    # Remove the app bundle so Login Items entry is fully cleaned up
    if _APP_BUNDLE.exists():
        import shutil
        shutil.rmtree(_APP_BUNDLE, ignore_errors=True)

    display.print_success("nudge watcher removed from login items")
    console.print("  [dim]Run 'nudge watch install' to re-enable auto-detection.[/dim]")


@watch_app.command("status")
def watch_status() -> None:
    """Show whether the auto-detect watcher is installed and running."""
    installed = _PLIST_PATH.exists()
    display.print_check("Login item installed", installed)

    if installed:
        result = subprocess.run(
            ["launchctl", "list", _PLIST_LABEL],
            capture_output=True,
            text=True,
        )
        running = result.returncode == 0 and '"PID"' in result.stdout
        display.print_check("Watcher process running", running)

        if _WATCHER_LOG.exists():
            console.print(f"\n  Last 5 log lines ({_WATCHER_LOG}):")
            lines = _WATCHER_LOG.read_text().splitlines()
            for line in lines[-5:]:
                console.print(f"  [dim]{line}[/dim]")
    else:
        console.print(
            "\n  [dim]Run 'nudge watch install' to enable auto-recording.[/dim]"
        )


@watch_app.command("logs")
def watch_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Show recent watcher log output."""
    if not _WATCHER_LOG.exists():
        display.print_info("No watcher log found. Is the watcher installed?")
        return

    log_lines = _WATCHER_LOG.read_text().splitlines()
    for line in log_lines[-lines:]:
        console.print(f"[dim]{line}[/dim]")
