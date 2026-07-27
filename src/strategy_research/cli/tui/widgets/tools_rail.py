"""ToolsRail — right-side panel showing Goal progress and Tool Timeline.

Replaces the old ActivityRail with a more structured layout:
- Goal Progress: shows current research goal, milestones, and progress
- Tool Timeline: shows recent tool calls with status and elapsed time

Layout:
    ┌──────────────────────────────────────────┐
    │ GOAL                                     │
    │ 研究A股低回撤量化策略                    │
    │ [======------] 55%  3/5                  │
    │ ● step_5 ⏳ running                     │
    │ ● step_4 ✔ done                         │
    ├──────────────────────────────────────────┤
    │ TOOLS                                    │
    │ ⏳ risk_analysis ...                     │
    │ ✔ backtest_result 5.2s                  │
    ├──────────────────────────────────────────┤
    │ 10/12 tools(2 running)                  │
    └──────────────────────────────────────────┘
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from textual.widget import Widget
from textual.widgets import Static

from strategy_research.cli.tui.theme import brand_tokens


@dataclass
class Milestone:
    """A single goal milestone/criteria."""
    name: str
    status: str  # "pending" | "running" | "done" | "failed" | "skipped"


@dataclass
class ToolEvent:
    """A single tool call event."""
    tool: str
    status: str  # "call" | "result" | "error"
    duration_ms: Optional[int] = None
    preview: str = ""


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


def _tool_icon(status: str) -> str:
    """Return icon for tool event status."""
    return {
        "call": "⏳",
        "result": "✔",
        "error": "✘",
    }.get(status, "·")


class ToolsRail(Static):
    """Right-panel showing Goal progress and Tool Timeline."""

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
        self._tools: List[ToolEvent] = []
        self._max_milestones: int = 5
        self._max_tools: int = 10

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

    def add_tool_event(self, event: ToolEvent) -> None:
        """Add a tool event to the timeline."""
        self._tools.append(event)
        # Keep only the last N events
        if len(self._tools) > self._max_tools:
            self._tools = self._tools[-self._max_tools:]
        self._refresh()

    def handle_event(self, event_type: str, data: dict) -> None:
        """Handle events from AgentLoop (tool_call / tool_result)."""
        if event_type == "tool_call":
            self.add_tool_event(ToolEvent(
                tool=data.get("tool", "?"),
                status="call",
            ))
        elif event_type == "tool_result":
            # Find the last matching tool with status "call" and update it
            for event in reversed(self._tools):
                if event.tool == data.get("tool") and event.status == "call":
                    event.status = "result" if data.get("ok", True) else "error"
                    event.duration_ms = data.get("elapsed_ms")
                    break
            self._refresh()

    def clear_tools(self) -> None:
        """Clear all tool events."""
        self._tools.clear()
        self._refresh()

    def _refresh(self) -> None:
        """Re-render the entire rail content."""
        parts: List[str] = []

        # Goal section
        parts.append(self._render_goal())

        # Separator
        parts.append("[dim]──────────────────────────────[/dim]")

        # Tools section
        parts.append(self._render_tools())

        # Footer
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

        # Milestones (newest first, max 5)
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

    def _render_tools(self) -> str:
        """Render the tools section."""
        lines: List[str] = []
        lines.append("[bold]TOOLS[/bold]")

        for event in self._tools[-self._max_tools:]:
            icon = _tool_icon(event.status)
            name = event.tool[:15] + "..." if len(event.tool) > 15 else event.tool
            duration = _format_duration(event.duration_ms)
            lines.append(f"{icon} {name}  {duration}")

        return "\n".join(lines)

    def _render_footer(self) -> str:
        """Render the footer with tool counts."""
        total = len(self._tools)
        running = sum(1 for t in self._tools if t.status == "call")
        done = sum(1 for t in self._tools if t.status == "result")
        error = sum(1 for t in self._tools if t.status == "error")

        if running > 0:
            return f"{done}/{total} tools({running} running)"
        return f"{total} tools"


def _progress_bar(value: int, max_value: int, width: int = 10) -> str:
    """Render a progress bar."""
    if max_value <= 0:
        return "[" + "-" * width + "]"
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


__all__ = ["ToolsRail", "Milestone", "ToolEvent"]
