"""StatusHeader — single-row header showing model, tokens, ctx, and stats.

Replaces the plain Textual Header with a status bar that displays:
- Connection status (● live / idle / error)
- Model name
- Message count + tool count
- Token usage + context window progress bar
- Success rate

Layout:
    ● live  minimax-M3  │ 5 msg  3 tool │ 1.2k/128k [====] │ 2/3 ok
"""
from __future__ import annotations

from typing import Any, Optional

from textual.widget import Widget
from textual.widgets import Static

from strategy_research.cli.tui.theme import brand_tokens


def _progress_bar(used: int, total: int, width: int = 10) -> str:
    """Render a progress bar like [====-------]."""
    if total <= 0:
        return "[" + "-" * width + "]"
    ratio = min(used / total, 1.0)
    filled = int(ratio * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def _format_token_count(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 500 -> '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _status_dot(status: str) -> str:
    """Return colored status dot."""
    if status == "live":
        return "[green]●[/green]"
    elif status == "error":
        return "[red]●[/red]"
    return "[dim]●[/dim]"


class StatusHeader(Static):
    """Single-row status header with model, tokens, ctx, and stats."""

    DEFAULT_CSS = """
    StatusHeader {
        height: 1;
        dock: top;
        background: $primary 25%;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._connection_status: str = "idle"
        self._model: str = "unknown"
        self._message_count: int = 0
        self._tool_count: int = 0
        self._tool_ok: int = 0
        self._token_used: int = 0
        self._token_total: int = 128000
        self._success_rate: str = ""

    def update_status(
        self,
        *,
        connection_status: Optional[str] = None,
        model: Optional[str] = None,
        message_count: Optional[int] = None,
        tool_count: Optional[int] = None,
        tool_ok: Optional[int] = None,
        token_used: Optional[int] = None,
        token_total: Optional[int] = None,
    ) -> None:
        """Update header fields and re-render."""
        if connection_status is not None:
            self._connection_status = connection_status
        if model is not None:
            self._model = model
        if message_count is not None:
            self._message_count = message_count
        if tool_count is not None:
            self._tool_count = tool_count
        if tool_ok is not None:
            self._tool_ok = tool_ok
        if token_used is not None:
            self._token_used = token_used
        if token_total is not None:
            self._token_total = token_total

        # Compute success rate
        if self._tool_count > 0:
            self._success_rate = f"{self._tool_ok}/{self._tool_count}"
        else:
            self._success_rate = "0/0"

        self._refresh()

    def _refresh(self) -> None:
        """Re-render the header content."""
        tokens = brand_tokens()

        # Build sections
        status_dot = _status_dot(self._connection_status)
        model = self._model
        msgs = f"{self._message_count} msg"
        tools = f"{self._tool_count} tool"
        token_str = f"{_format_token_count(self._token_used)}/{_format_token_count(self._token_total)}"
        bar = _progress_bar(self._token_used, self._token_total, width=8)
        rate = self._success_rate

        # Compose with separators
        content = (
            f"{status_dot} {model}"
            f"  [dim]│[/dim]  {msgs}  {tools}"
            f"  [dim]│[/dim]  {token_str} {bar}"
            f"  [dim]│[/dim]  {rate}"
        )

        self.update(content)


__all__ = ["StatusHeader"]
