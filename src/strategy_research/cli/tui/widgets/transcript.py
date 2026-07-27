"""TranscriptView - scrolling log of assistant turns + system messages.

Wraps Textual's :class:`textual.widgets.RichLog` so we can feed it Rich
``RenderableType`` objects (the output of ``cli.ui.transcript.render_answer``)
directly. The widget auto-scrolls to the latest line; consumers post a
``WriteTranscript`` message which the parent app forwards to ``write()``.

Streaming + fold (TUI display philosophy):
    1. **Streaming**: ``begin_streaming()`` records a line baseline,
       ``update_streaming(text)`` truncates back to that baseline and
       re-writes the folded tail.  No new lines accumulate.

    2. **Folded record**: ``end_streaming(suffix)`` converts the
       streamer into a *folder* appended to a list.  Each folder is
       independently toggleable via ``toggle_fold()`` (Ctrl+E).

    3. **Auto-fold**: ``begin_streaming()`` auto-folds the currently
       active (expanded) folder so the screen stays compact.

Multi-folder cursor (Ctrl+E):
    Ctrl+E cycles through all folders.  The first press activates the
    last folder (expand).  Subsequent presses fold the current one and
    expand the previous (cyclic).  This lets the user inspect any past
    turn without losing context.
"""
from __future__ import annotations

from typing import Any, Iterable

from rich.console import RenderableType
from textual.widgets import RichLog

from strategy_research.cli.tui.messages import WriteTranscript
from strategy_research.cli.tui.widgets.streaming_text import StreamingText


class TranscriptView(RichLog):
    """Auto-scrolling chat log widget with streaming + fold support.

    Public API:
        write(content): append a Renderable on a new line.
        begin_streaming(): start an in-place streaming session.
        update_streaming(text): replace streaming content in-place.
        end_streaming(suffix) -> str: finalize, return full text.
        toggle_fold(): cycle fold/expand through folders (Ctrl+E).
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
        self._stream_baseline: int | None = None
        self._streamer: StreamingText | None = None
        self._folders: list[StreamingText] = []
        self._fold_baselines: list[int] = []
        self._active_folder_idx: int | None = None

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
        self._stream_baseline = None
        self._streamer = None
        self._folders = []
        self._fold_baselines = []
        self._active_folder_idx = None

    # ------------------------------------------------------------------ streaming

    def begin_streaming(self) -> None:
        """Start an in-place streaming session.

        Auto-folds the currently active (expanded) folder so the screen
        stays compact before the new turn begins.
        """
        if self._active_folder_idx is not None:
            idx = self._active_folder_idx
            if self._folders[idx].expanded:
                self._re_render_folder(idx, expand=False)
        self._active_folder_idx = None
        self._stream_baseline = len(self.lines)
        self._streamer = StreamingText()
        self._streamer.start()

    def update_streaming(self, text: str) -> None:
        """Replace streaming content in-place (no new lines accumulated)."""
        if self._streamer is None:
            return
        self._streamer.update_streaming(text)
        self._truncate_to(self._stream_baseline)
        rendered = self._streamer.render()
        if rendered:
            self.write(rendered)

    def end_streaming(self, suffix: str = "") -> str:
        """Finalize streaming session.

        Appends the streamer as a new folder to the list.
        ``suffix`` (e.g. stats line) is appended to the rendered text.

        Returns the full accumulated text.
        """
        if self._streamer is None:
            return ""
        full_text = self._streamer.full_text
        self._truncate_to(self._stream_baseline)
        self._folders.append(self._streamer)
        self._fold_baselines.append(self._stream_baseline)
        self._streamer = None
        self._stream_baseline = None
        rendered = self._folders[-1].render()
        if rendered:
            if suffix:
                self.write(f"{rendered}  {suffix}")
            else:
                self.write(rendered)
        elif suffix:
            self.write(suffix)
        return full_text

    # ------------------------------------------------------------------ fold

    def toggle_fold(self) -> None:
        """Cycle fold/expand through folders (Ctrl+E).

        * No active folder: activate the last folder, expand it.
        * Active folder expanded: fold it, move cursor to the previous
          folder (cyclic), expand that one.
        * Active folder folded: expand it.
        """
        if not self._folders:
            return
        if self._active_folder_idx is None:
            self._active_folder_idx = len(self._folders) - 1
            self._re_render_folder(self._active_folder_idx, expand=True)
        else:
            idx = self._active_folder_idx
            folder = self._folders[idx]
            if folder.expanded:
                self._re_render_folder(idx, expand=False)
                self._active_folder_idx = (idx - 1) % len(self._folders)
                self._re_render_folder(self._active_folder_idx, expand=True)
            else:
                self._re_render_folder(idx, expand=True)

    # ------------------------------------------------------------------ internal

    def _re_render_folder(self, idx: int, expand: bool) -> None:
        """Re-render a single folder in-place.

        Truncates lines to the folder's baseline, writes the new
        (folded/expanded) content, then restores subsequent lines
        and adjusts later baselines for the line-count delta.
        """
        start = self._fold_baselines[idx]
        if idx + 1 < len(self._fold_baselines):
            end = self._fold_baselines[idx + 1]
        else:
            end = len(self.lines)
        after = self.lines[end:]
        self._truncate_to(start)
        folder = self._folders[idx]
        if expand:
            folder.expand()
        else:
            folder.collapse()
        rendered = folder.render()
        if rendered:
            self.write(rendered)
        old_end = end
        new_end = len(self.lines)
        self.lines.extend(after)
        delta = new_end - old_end
        for i in range(idx + 1, len(self._fold_baselines)):
            self._fold_baselines[i] += delta
        self._line_cache.clear()
        self.virtual_size = self.virtual_size.__class__(
            self._widest_line_width, len(self.lines)
        )
        self.refresh()

    def _truncate_to(self, baseline: int | None) -> None:
        """Truncate ``self.lines`` back to ``baseline``."""
        if baseline is None:
            return
        if len(self.lines) > baseline:
            self.lines = self.lines[:baseline]
            self._line_cache.clear()
            self.virtual_size = (
                self.virtual_size.__class__(
                    self._widest_line_width, len(self.lines)
                )
            )
            self.refresh()

    # ------------------------------------------------------------------ messages

    def on_write_transcript(self, message: WriteTranscript) -> None:
        """Textual message handler: dispatch ``WriteTranscript`` to ``write``."""
        self.write(message.content)
