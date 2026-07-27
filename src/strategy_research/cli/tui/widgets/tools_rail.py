"""ToolsRail - right-side panel showing Goal progress + unified Timeline.

Replaces the old GOAL+TOOLS split with a single timeline that shows
every agent action in chronological order (vibe-trading style):

    ┌──────────────────────────────────────┐
    │ GOAL                                 │
    │ 研究A股低回撤量化策略                │
    │ [======------] 55%  3/5              │
    ├──────────────────────────────────────┤
    │ TIMELINE                             │
    │ • backtest 5.2s                      │
    │   └ progress: 50% done               │
    │ • compacted (12k→4k)                 │
    │ ⏳ risk_analysis ...                 │
    ├──────────────────────────────────────┤
    │ iter 2/10  3 tools                   │
    └──────────────────────────────────────┘
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from textual.widgets import Static

from strategy_research.cli.tui.theme import brand_tokens


@dataclass
class Milestone:
    """A single goal milestone/criteria."""
    name: str
    status: str  # "pending" | "running" | "done" | "failed" | "skipped"


@dataclass
class TimelineEntry:
    """A single entry in the unified timeline."""
    kind: str  # "tool" | "compact" | "info"
    label: str  # tool name or compact summary
    status: str  # "running" | "done" | "error" | "info"
    duration_ms: Optional[int] = None
    detail: str = ""  # last progress detail (shown as └ sub-line)
    iter: Optional[int] = None


def _format_duration(ms: Optional[int]) -> str:
    """Format duration: 1234 -> '1.2s', 500 -> '500ms'."""
    if ms is None:
        return "..."
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _milestone_icon(status: str) -> str:
    """Return icon for milestone status."""
    return {
        "pending": "○",
        "running": "⏳",
        "done": "✔",
        "failed": "✘",
        "skipped": "○",
    }.get(status, "○")


def _entry_icon(status: str) -> str:
    """Return icon for timeline entry status."""
    return {
        "running": "⏳",
        "done": "•",
        "error": "✘",
        "info": "•",
    }.get(status, "•")


class ToolsRail(Static):
    """Right-panel showing Goal progress + unified Timeline."""

    DEFAULT_CSS = """
    ToolsRail {
        width: 36;
        height: 1fr;
        border: round $primary;
        border-title-align: left;
        background: $primary 8%;
        padding: 0 1;
    }
    """

    BORDER_TITLE = "Tools"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._goal_title: str = ""
        self._goal_description: str = ""
        self._goal_progress: int = 0
        self._goal_criteria_total: int = 0
        self._goal_criteria_done: int = 0
        self._milestones: List[Milestone] = []
        self._timeline: List[TimelineEntry] = []
        self._max_milestones: int = 5
        self._max_timeline: int = 12
        self._iter: int = 0
        self._iter_max: int = 0

    def update_goal(
        self,
        *,
        title: str = "",
        description: str = "",
        progress: int = 0,
        criteria_total: int = 0,
        criteria_done: int = 0,
        milestones: Optional[List[Milestone]] = None,
    ) -> None:
        """Update goal progress information."""
        if title:
            self._goal_title = title
        if description:
            self._goal_description = description
        self._goal_progress = progress
        self._goal_criteria_total = criteria_total
        self._goal_criteria_done = criteria_done
        if milestones is not None:
            self._milestones = milestones
        self._refresh()

    def set_iter(self, iteration: int, max_iterations: int = 0) -> None:
        """Update the current iteration counter."""
        self._iter = iteration
        if max_iterations:
            self._iter_max = max_iterations
        self._refresh()

    def handle_event(self, event_type: str, data: dict) -> None:
        """Handle events from AgentLoop."""
        if event_type == "tool_call":
            self._timeline.append(TimelineEntry(
                kind="tool",
                label=data.get("tool", "?"),
                status="running",
                iter=data.get("iter"),
            ))
            self._trim_timeline()
            self._refresh()
        elif event_type == "tool_result":
            tool_name = data.get("tool", "?")
            for entry in reversed(self._timeline):
                if entry.kind == "tool" and entry.label == tool_name and entry.status == "running":
                    entry.status = "done" if data.get("status", "ok") == "ok" else "error"
                    if data.get("status") == "error":
                        entry.status = "error"
                    entry.duration_ms = data.get("elapsed_ms")
                    break
            self._refresh()
        elif event_type == "tool_progress":
            detail = data.get("detail", data.get("message", ""))
            if not detail:
                return
            for entry in reversed(self._timeline):
                if entry.kind == "tool" and entry.status == "running":
                    entry.detail = detail
                    break
            self._refresh()
        elif event_type == "tool_heartbeat":
            for entry in reversed(self._timeline):
                if entry.kind == "tool" and entry.status == "running":
                    if data.get("detail"):
                        entry.detail = data["detail"]
                    break
            self._refresh()
        elif event_type == "compact":
            before = data.get("before_tokens", "?")
            after = data.get("after_tokens", "?")
            self._timeline.append(TimelineEntry(
                kind="compact",
                label=f"compacted ({before}k→{after}k)",
                status="info",
                iter=data.get("iter"),
            ))
            self._trim_timeline()
            self._refresh()
        elif event_type == "iter_start":
            self.set_iter(data.get("iteration", 0), data.get("max_iterations", 0))
        elif event_type == "iter_end":
            pass

    def clear_timeline(self) -> None:
        """Clear all timeline entries."""
        self._timeline.clear()
        self._refresh()

    def _trim_timeline(self) -> None:
        if len(self._timeline) > self._max_timeline:
            self._timeline = self._timeline[-self._max_timeline:]

    def _refresh(self) -> None:
        """Re-render the entire rail content."""
        parts: List[str] = []
        parts.append(self._render_goal())
        parts.append("[dim]──────────────────────────────[/dim]")
        parts.append(self._render_timeline())
        parts.append(self._render_footer())
        content = "\n".join(parts)
        self.update(content)

    def _render_goal(self) -> str:
        """Render the goal section."""
        lines: List[str] = []
        lines.append("[bold]GOAL[/bold]")

        if self._goal_description:
            desc = self._goal_description[:20]
            if len(self._goal_description) > 20:
                desc += "..."
            lines.append(desc)

        if self._goal_criteria_total > 0:
            bar = _progress_bar(self._goal_progress, 100, width=12)
            lines.append(
                f"{bar} {self._goal_progress}%  "
                f"{self._goal_criteria_done}/{self._goal_criteria_total}"
            )

        visible = self._milestones[:self._max_milestones]
        for m in visible:
            icon = _milestone_icon(m.status)
            name = m.name[:15] + "..." if len(m.name) > 15 else m.name
            lines.append(f"{icon} {name}  {m.status}")

        if len(self._milestones) > self._max_milestones:
            lines.append(
                f"[dim]↑↓ scroll  {self._max_milestones}/{len(self._milestones)} shown[/dim]"
            )

        return "\n".join(lines)

    def _render_timeline(self) -> str:
        """Render the unified timeline section."""
        lines: List[str] = []
        lines.append("[bold]TIMELINE[/bold]")

        for entry in self._timeline[-self._max_timeline:]:
            icon = _entry_icon(entry.status)
            if entry.status == "running":
                lines.append(f"{icon} {entry.label} ...")
            else:
                duration = _format_duration(entry.duration_ms)
                lines.append(f"{icon} {entry.label} {duration}")
            if entry.detail:
                lines.append(f"  [dim]└ {entry.detail}[/dim]")

        if not self._timeline:
            lines.append("[dim](idle)[/dim]")

        return "\n".join(lines)

    def _render_footer(self) -> str:
        """Render the footer with iter + tool counts."""
        parts: List[str] = []
        if self._iter_max > 0:
            parts.append(f"iter {self._iter}/{self._iter_max}")
        total = len(self._timeline)
        running = sum(1 for t in self._timeline if t.status == "running")
        done = sum(1 for t in self._timeline if t.status == "done")
        if running > 0:
            parts.append(f"{done}/{total} tools({running} running)")
        elif total > 0:
            parts.append(f"{total} tools")
        return "  ".join(parts) if parts else ""


def _progress_bar(value: int, max_value: int, width: int = 10) -> str:
    """Render a progress bar."""
    if max_value <= 0:
        return "[" + "-" * width + "]"
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


__all__ = ["ToolsRail", "Milestone", "TimelineEntry"]
