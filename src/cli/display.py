"""
Rich display helpers for nudge CLI.
All terminal output goes through this module for consistency.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from rich import print as rprint
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

console = Console()


# ── Banners ───────────────────────────────────────────────────────────────────

def print_banner() -> None:
    console.print()
    console.print(Panel.fit(
        "[bold]nudge[/bold]  ·  your silent meeting scribe",
        border_style="dim",
        padding=(0, 2),
    ))
    console.print()


def print_recording_status(device: str, title: str) -> None:
    console.print(
        f"[bold green]●[/bold green] Recording · [dim]{title}[/dim]"
    )
    console.print(f"  [dim]Device: {device}[/dim]")
    console.print(f"  [dim]Ctrl+C to stop[/dim]")
    console.print()


# ── Live transcript ───────────────────────────────────────────────────────────

def print_transcript_line(offset_seconds: float, text: str) -> None:
    """Print a single transcribed line with timestamp prefix."""
    m = int(offset_seconds // 60)
    s = int(offset_seconds % 60)
    console.print(f"[dim][{m:02d}:{s:02d}][/dim] {text.strip()}")


# ── Processing ────────────────────────────────────────────────────────────────

def print_processing_header(title: str) -> None:
    console.print()
    console.print("─" * 48)
    console.print(f"[bold]Processing:[/bold] {title}")
    console.print()


def make_progress() -> Progress:
    """Create a Rich progress bar for multi-step processing."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description:<30}"),
        BarColumn(bar_width=24),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


# ── Action summary ────────────────────────────────────────────────────────────

def print_action_summary(
    actions: list[dict],
    added: int,
    skipped: int,
    list_name: str,
    notes_path: Optional[str] = None,
) -> None:
    console.print()
    console.print("─" * 48)

    if not actions:
        console.print("[dim]No action items detected in this meeting.[/dim]")
        return

    qualifying = [a for a in actions if a.get("confidence", 0) >= 0.5]

    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
        show_edge=False,
    )
    table.add_column("Conf", style="dim", width=5, no_wrap=True)
    table.add_column("Task", min_width=30)
    table.add_column("Owner", style="cyan", width=14, no_wrap=True)
    table.add_column("Due", style="yellow", width=12, no_wrap=True)

    for action in sorted(qualifying, key=lambda a: a.get("confidence", 0), reverse=True):
        conf = action.get("confidence", 0)
        conf_str = f"{conf:.0%}"
        if conf >= 0.85:
            conf_style = "bold green"
        elif conf >= 0.65:
            conf_style = "green"
        else:
            conf_style = "yellow"

        table.add_row(
            Text(conf_str, style=conf_style),
            action.get("task", ""),
            (action.get("assignee") or "—")[:14],
            (action.get("deadline") or "—")[:12],
        )

    console.print(table)
    console.print()

    if added > 0:
        console.print(
            f"[bold green]✓[/bold green] {added} item{'s' if added != 1 else ''} "
            f"added to Reminders → [italic]\"{list_name}\"[/italic]"
        )
    if skipped > 0:
        console.print(
            f"[dim]  {skipped} skipped (below confidence threshold)[/dim]"
        )
    if notes_path:
        console.print(
            f"[bold green]✓[/bold green] Meeting notes saved → [italic]{notes_path}[/italic]"
        )

    console.print()


# ── Session list ──────────────────────────────────────────────────────────────

def print_session_list(sessions: list) -> None:
    if not sessions:
        console.print("[dim]No sessions found. Run 'nudge start' to begin recording.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
        show_edge=False,
    )
    table.add_column("ID", style="dim", width=8, no_wrap=True)
    table.add_column("Date", width=17, no_wrap=True)
    table.add_column("Title", min_width=24)
    table.add_column("Duration", width=10, no_wrap=True)
    table.add_column("Status", width=12, no_wrap=True)
    table.add_column("Actions", width=8, no_wrap=True)

    for s in sessions:
        duration = ""
        if s.stopped_at:
            secs = s.duration_seconds
            duration = f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
        elif s.status.value == "recording":
            duration = "[blink]●[/blink] live"

        status_colors = {
            "complete": "green",
            "recording": "yellow",
            "error": "red",
            "processing": "blue",
            "stopped": "dim",
        }
        status_color = status_colors.get(s.status.value, "white")

        table.add_row(
            s.id,
            s.started_at.strftime("%b %d  %H:%M"),
            s.title or "[dim](untitled)[/dim]",
            duration,
            Text(s.status.value, style=status_color),
            "—",
        )

    console.print(table)


# ── Search results ────────────────────────────────────────────────────────────

def print_search_results(results: list[dict], query: str) -> None:
    if not results:
        console.print(f"[dim]No results for \"{query}\"[/dim]")
        return

    console.print(f"\n[bold]{len(results)} result(s) for \"{query}\"[/bold]\n")
    for r in results:
        started = datetime.fromisoformat(r["started_at"])
        offset = r.get("started_at_offset", 0)
        m, s = int(offset // 60), int(offset % 60)

        console.print(
            f"[cyan]{started.strftime('%b %d')}[/cyan]  "
            f"[dim]{r.get('title', '(untitled)')}[/dim]  "
            f"[dim][{m:02d}:{s:02d}][/dim]"
        )
        # Highlight query in text
        text = r.get("text", "")
        highlighted = text.replace(query, f"[bold yellow]{query}[/bold yellow]")
        console.print(f"  {highlighted[:200]}")
        console.print()


# ── Digest ────────────────────────────────────────────────────────────────────

def print_digest(digest: dict, period: str) -> None:
    console.print(Panel.fit(
        f"[bold]Weekly Digest[/bold]  ·  {period}",
        border_style="dim",
    ))
    console.print()

    if digest.get("week_summary"):
        console.print(f"[bold]This week:[/bold] {digest['week_summary']}")
        console.print()

    if digest.get("key_themes"):
        console.print("[bold]Recurring themes:[/bold]")
        for theme in digest["key_themes"]:
            console.print(f"  · {theme}")
        console.print()

    if digest.get("critical_actions"):
        console.print("[bold]Top action items still open:[/bold]")
        for action in digest["critical_actions"]:
            console.print(f"  [red]→[/red] {action}")
        console.print()

    if digest.get("wins"):
        console.print("[bold]Wins:[/bold]")
        for win in digest["wins"]:
            console.print(f"  [green]✓[/green] {win}")
        console.print()


# ── Doctor ────────────────────────────────────────────────────────────────────

def print_check(label: str, ok: bool, note: str = "") -> None:
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    line = f"  {icon}  {label}"
    if note:
        line += f"  [dim]{note}[/dim]"
    console.print(line)


# ── Utility ───────────────────────────────────────────────────────────────────

def print_error(msg: str) -> None:
    console.print(f"\n[bold red]Error:[/bold red] {msg}\n")


def print_success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green]  {msg}")


def print_warn(msg: str) -> None:
    console.print(f"[yellow]⚠[/yellow]   {msg}")


def print_info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")
