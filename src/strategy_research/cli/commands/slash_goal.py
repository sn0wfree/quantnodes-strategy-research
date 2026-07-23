"""``/goal`` slash-command shims.

Thin command wrappers around :class:`GoalStore` for the interactive REPL:

* :func:`cmd_status` (``/goal`` or ``/goal status``) — render current snapshot.
* :func:`cmd_start` (``/goal start <objective>``) — create a new goal.
* :func:`cmd_evidence` (``/goal evidence <idx> <note>``) — append evidence.
* :func:`cmd_complete` (``/goal complete [recap]``) — mark complete.
* :func:`cmd_cancel` (``/goal cancel [recap]``) — cancel.
* :func:`cmd_help` (``/goal help``) — usage panel.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from strategy_research.cli.theme import get_console


def _resolve_db_path() -> Path:
    """Return the goal DB path. Default to ``<cwd>/goals.db``."""
    raw = os.environ.get("STRATEGY_RESEARCH_GOAL_DB")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "goals.db"


def _resolve_console(console: Optional[Console] = None) -> Console:
    return console or get_console()


def _store():
    from strategy_research.core.goal import GoalStore
    return GoalStore(db_path=_resolve_db_path())


def cmd_status(*, console: Optional[Console] = None, session_id: str = "cli") -> int:
    """``/goal`` — render current goal snapshot."""
    console = _resolve_console(console)
    try:
        store = _store()
        snapshot = store.get_current_snapshot(session_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal status failed:[/red] {exc}")
        return 1

    if not snapshot:
        console.print("[yellow]No active goal. Use /goal start <objective>.[/yellow]")
        return 0

    goal = snapshot.get("goal", {})
    title = goal.get("objective", "(no objective)")
    console.print(Panel(
        f"[bold]{title}[/bold]\n"
        f"status: {goal.get('status')}\n"
        f"protocol: {goal.get('protocol')}\n"
        f"created: {goal.get('created_at', '')[:19]}",
        title=f"Goal {goal.get('goal_id', '')[:12]}",
        border_style="blue",
    ))

    criteria = snapshot.get("criteria", [])
    if criteria:
        table = Table(show_header=True, header_style="bold")
        table.add_column("#")
        table.add_column("Criterion")
        table.add_column("Status")
        for i, c in enumerate(criteria, 1):
            table.add_row(str(i), str(c.get("text", ""))[:60], str(c.get("status", "")))
        console.print(table)
    return 0


def cmd_start(objective: str, *, console: Optional[Console] = None,
              session_id: str = "cli") -> int:
    """``/goal start <objective>`` — create a new goal."""
    console = _resolve_console(console)
    if not objective.strip():
        console.print("[red]Usage:[/red] /goal start <objective>")
        return 1
    try:
        from strategy_research.core.goal.context import default_goal_criteria

        store = _store()
        criteria = default_goal_criteria()
        goal = store.replace_goal(
            session_id=session_id,
            objective=objective,
            criteria=criteria,
            source="cli",
            protocol="thesis_review",
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal start failed:[/red] {exc}")
        return 1
    console.print(f"[green]Started goal:[/green] {goal.goal_id}")
    return 0


def cmd_evidence(criterion_ref: str, note: str, *,
                 console: Optional[Console] = None,
                 session_id: str = "cli") -> int:
    """``/goal evidence <idx-or-id> <note>``."""
    console = _resolve_console(console)
    if not criterion_ref or not note.strip():
        console.print("[red]Usage:[/red] /goal evidence <idx-or-id> <note>")
        return 1
    try:
        from strategy_research.core.goal.models import EvidenceInput

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0

        goal_id = snapshot["goal"]["goal_id"]
        criteria = snapshot.get("criteria", [])
        # Resolve criterion_ref — 1-based index or exact id or prefix
        criterion_id = None
        try:
            idx = int(criterion_ref) - 1
            if 0 <= idx < len(criteria):
                criterion_id = criteria[idx].get("criterion_id")
        except ValueError:
            pass
        if criterion_id is None:
            for c in criteria:
                cid = str(c.get("criterion_id", ""))
                if cid == criterion_ref or cid.startswith(criterion_ref):
                    criterion_id = cid
                    break
        if criterion_id is None:
            console.print(f"[red]Unknown criterion:[/red] {criterion_ref}")
            return 1

        store.append_evidence(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            evidence=EvidenceInput(
                criterion_id=criterion_id,
                text=note,
                source_provider="cli",
                source_type="manual_note",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal evidence failed:[/red] {exc}")
        return 1
    console.print("[green]Evidence recorded.[/green]")
    return 0


def cmd_complete(recap: str = "", *, console: Optional[Console] = None,
                 session_id: str = "cli") -> int:
    """``/goal complete [recap]`` — mark the goal complete."""
    console = _resolve_console(console)
    try:
        from strategy_research.core.goal.models import AuditRow, GoalStatus

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0
        goal_id = snapshot["goal"]["goal_id"]
        # Verify all criteria have evidence before allowing completion
        criteria = snapshot.get("criteria", [])
        all_covered = snapshot.get("all_covered", False)
        if criteria and not all_covered:
            console.print("[red]Cannot complete:[/red] not all criteria have evidence.")
            return 1

        evidence_ids = [e.get("evidence_id", "") for e in snapshot.get("evidence", [])]
        store.update_status(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            status=GoalStatus.COMPLETE,
            audit=AuditRow(result="satisfied", evidence_ids=evidence_ids, notes=recap or ""),
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal complete failed:[/red] {exc}")
        return 1
    console.print("[green]Goal completed.[/green]")
    return 0


def cmd_cancel(recap: str = "", *, console: Optional[Console] = None,
               session_id: str = "cli") -> int:
    """``/goal cancel [recap]``."""
    console = _resolve_console(console)
    try:
        from strategy_research.core.goal.models import GoalStatus

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0
        goal_id = snapshot["goal"]["goal_id"]
        store.update_status(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            status=GoalStatus.CANCELLED,
            recap=recap,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal cancel failed:[/red] {exc}")
        return 1
    console.print("[yellow]Goal cancelled.[/yellow]")
    return 0


def cmd_help(*, console: Optional[Console] = None) -> int:
    """``/goal help`` — usage panel."""
    console = _resolve_console(console)
    body = (
        "/goal status                       — show current goal\n"
        "/goal start <objective>            — create a new goal\n"
        "/goal evidence <idx-or-id> <note>  — record evidence\n"
        "/goal complete [recap]             — mark complete\n"
        "/goal cancel [recap]               — cancel\n"
    )
    console.print(Panel(body, title="/goal", border_style="dim"))
    return 0


# Slash-router entrypoint
def run(ctx: Any = None, *args: str) -> int:
    """Router for ``/goal`` subcommands."""
    if not args:
        return cmd_status()
    sub = args[0]
    rest = list(args[1:])
    if sub == "status":
        return cmd_status()
    if sub == "help":
        return cmd_help()
    if sub == "start":
        return cmd_start(" ".join(rest))
    if sub == "evidence" and rest:
        # Next tokens: "<idx-or-id>" "<note words...>"
        if len(rest) >= 2:
            return cmd_evidence(rest[0], " ".join(rest[1:]))
        return cmd_evidence(rest[0], "")
    if sub == "complete":
        return cmd_complete(" ".join(rest))
    if sub == "cancel":
        return cmd_cancel(" ".join(rest))
    return cmd_help()


__all__ = [
    "cmd_status",
    "cmd_start",
    "cmd_evidence",
    "cmd_complete",
    "cmd_cancel",
    "cmd_help",
    "run",
]
