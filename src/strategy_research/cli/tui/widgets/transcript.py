"""TranscriptView — scrolling log of assistant turns + system messages.

Wraps Textual's :class:`textual.widgets.RichLog` so we can feed it Rich
``RenderableType`` objects (the output of ``cli.ui.transcript.render_answer``)
directly. The widget auto-scrolls to the latest line; consumers post a
``WriteTranscript`` message which the parent app forwards to ``write()``.

The historical REPL wrote lines to a Rich ``Console``; the TUI equivalent
is "post a message → ``RichLog.write(content)``". Both share the same
underlying ``Renderable`` instances, so the visual output is identical.
"""
from __future__ import annotations

from typing import Any, Iterable

from rich.console import RenderableType
from textual.widgets import RichLog

from strategy_research.cli.tui.messages import WriteTranscript


class TranscriptView(RichLog):
    """Auto-scrolling chat log widget.

    Public API:
        write(content): append a Renderable on a new line.
        write_line(content): same, but for single ``str`` / ``Text`` lines.
    """

    DEFAULT_CSS = """
    TranscriptView {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    BORDER_TITLE = "Transcript"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(wrap=True, markup=True, highlight=False, **kwargs)

    # NOTE: We do NOT override ``write`` — Textual's :class:`RichLog.write`
    # takes up to 5 positional args (``content, width, expand, shrink,
    # scroll_end``). Overriding it with a narrower signature breaks the
    # deferred-render path that uses ``self.write(*deferred_render)`` on
    # resize. We expose ``append`` instead.

    def append(self, content: RenderableType | str) -> None:
        """Append ``content`` on a new line (alias for ``write``)."""
        self.write(content)

    def write_lines(self, lines: Iterable[RenderableType | str]) -> None:
        """Append a batch of lines."""
        for ln in lines:
            self.write(ln)

    def clear_log(self) -> None:
        """Drop all lines (used by ``ctrl+l`` action)."""
        self.clear()

    def on_write_transcript(self, message: WriteTranscript) -> None:
        """Textual message handler: dispatch ``WriteTranscript`` to ``write``."""
        self.write(message.content)
