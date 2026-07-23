"""``/history``, ``/search``, ``/export`` slash-command shims.

These are *slash command* entrypoints used by the interactive REPL; the
legacy argparse-based session commands (``cmd_session_*``) live in
``cli.commands.session`` and are unrelated.

* :func:`cmd_history` (``/history``) — list recent sessions via ``SessionDB``.
* :func:`cmd_search` (``/search <query>``) — FTS5 search across messages.
* :func:`cmd_export` (``/export``) — placeholder pointing at the web UI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from strategy_research.cli.theme import get_console

_PATH_ENV = "STRATEGY_RESEARCH_SESSIONS_DB"


def _resolve_db_path() -> Optional[str]:
    """Find the sessions DB path. Returns ``None`` if env unset."""
    return os.environ.get(_PATH_ENV) or None


def _resolve_console(console: Optional[Console] = None) -> Console:
    return console or get_console()


def cmd_history(*, console: Optional[Console] = None, limit: int = 10) -> int:
    """``/history`` — list the most recent sessions."""
    console = _resolve_console(console)
    db_path = _resolve_db_path()
    if not db_path:
        console.print("[yellow]STRATEGY_RESEARCH_SESSIONS_DB not set; nothing to list.[/yellow]")
        return 0
    try:
        from strategy_research.core.session.db import SessionDB
        db = SessionDB(db_path)
        sessions = db.list_sessions(limit=limit)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/history failed:[/red] {exc}")
        return 1

    if not sessions:
        console.print("[yellow]No prior sessions found.[/yellow]")
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Status")
    for s in sessions:
        sid = getattr(s, "session_id", "?")
        title = (getattr(s, "title", "") or "")[:60]
        status = str(getattr(s, "status", ""))
        table.add_row(sid[:12], title, status)
    console.print(table)
    return 0


def cmd_search(query: str, *, console: Optional[Console] = None, limit: int = 10) -> int:
    """``/search <query>`` — FTS5 search over session messages."""
    console = _resolve_console(console)
    if not query.strip():
        console.print("[red]Usage:[/red] /search <query>")
        return 1
    db_path = _resolve_db_path()
    if not db_path:
        console.print("[yellow]STRATEGY_RESEARCH_SESSIONS_DB not set; nothing to search.[/yellow]")
        return 0
    try:
        from strategy_research.core.session.db import SessionDB
        db = SessionDB(db_path)
        matches = db.search_messages(query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/search failed:[/red] {exc}")
        return 1

    if not matches:
        console.print(f"[yellow]No matches for '{query}'.[/yellow]")
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("Session")
    table.add_column("Role")
    table.add_column("Snippet")
    for m in matches:
        sid = str(getattr(m, "session_id", "?"))[:12]
        role = str(getattr(m, "role", ""))
        snippet = (str(getattr(m, "content", "")) or "")[:80]
        table.add_row(sid, role, snippet)
    console.print(table)
    return 0


def cmd_export(*, console: Optional[Console] = None) -> int:
    """``/export`` — placeholder pointing at the web UI's md/json export."""
    console = _resolve_console(console)
    body = Text()
    body.append("/export is not yet wired up to the interactive CLI.\n\n", style="dim")
    body.append("Until then: ", style="dim")
    body.append("the web UI exports md / json from the message footer.", style="bold")
    console.print(Panel(body, title="/export", border_style="dim"))
    return 0


# Slash-router entrypoints
def run(ctx: Any = None, *args: str) -> int:
    """Router for /history, /search, /export subcommands.

    With no args → cmd_export (placeholder).
    With one or more args → cmd_search(query=" ".join(args)).
    """
    if not args:
        return cmd_export()
    return cmd_search(" ".join(args))


__all__ = [
    "cmd_history",
    "cmd_search",
    "cmd_export",
    "run",
]
