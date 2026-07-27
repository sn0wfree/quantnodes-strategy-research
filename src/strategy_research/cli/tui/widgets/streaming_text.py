"""StreamingText - pure-logic streaming text accumulator + fold renderer.

Provides the text accumulation and fold/expand rendering logic used by
:class:`TranscriptView`. This is NOT a widget - it has no visual
representation. The host widget (:class:`TranscriptView`) calls
``render()`` and writes the returned string to its own RichLog surface.

Implements principles 2 (fold, not discard) and 3 (in-place replacement)
from the TUI display philosophy.

Render layout (when text exceeds ``_DISPLAY_CHARS``):

    [bold]一句话总结…[/bold]
    [muted]↑ +N chars (ctrl+e to expand)[/muted]
    ...tail content (last 200 chars)...

When expanded:

    [bold]一句话总结…[/bold]
    [muted]↓ N chars (ctrl+e to fold)[/muted]
    ...full text...

Lifecycle:
    * ``render()`` returns a truncated view with summary + fold indicator.
    * ``toggle_expand()`` / ``expand()`` / ``collapse()`` control state.
"""
from __future__ import annotations

_SUMMARY_CHARS = 60
_DISPLAY_CHARS = 200


class StreamingText:
    """Text accumulator with fold/expand rendering.

    ``full_text`` is always the complete accumulated content; the
    ``render()`` method controls how much is *displayed*.
    """

    def __init__(self) -> None:
        self.full_text: str = ""
        self._expanded: bool = False

    def start(self) -> None:
        self.full_text = ""
        self._expanded = False

    def update_streaming(self, text: str) -> None:
        self.full_text = text

    def append_delta(self, delta: str) -> None:
        self.full_text += delta

    def stop(self) -> None:
        self.full_text = ""
        self._expanded = False

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded

    def expand(self) -> None:
        self._expanded = True

    def collapse(self) -> None:
        self._expanded = False

    @property
    def expanded(self) -> bool:
        return self._expanded

    def _summary(self) -> str:
        """Extract a one-sentence summary (first sentence, capped at 60 chars)."""
        text = self.full_text.strip()
        if not text:
            return ""
        for sep in ("\n", "。", ". ", "！", "？", "!", "?"):
            idx = text.find(sep)
            if 0 < idx <= _SUMMARY_CHARS:
                s = text[:idx].strip()
                return s + "…" if idx < len(text) else s
        if len(text) <= _SUMMARY_CHARS:
            return text
        return text[:_SUMMARY_CHARS].strip() + "…"

    def render(self) -> str:
        """Return the display string (folded or expanded)."""
        text = self.full_text
        if not text:
            return ""
        if len(text) <= _DISPLAY_CHARS:
            return text
        summary = self._summary()
        if self._expanded:
            return (
                f"[bold]{summary}[/bold]\n"
                f"[muted]\u2193 {len(text)} chars "
                f"(ctrl+e to fold)[/muted]\n"
                f"{text}"
            )
        hidden = len(text) - _DISPLAY_CHARS
        return (
            f"[bold]{summary}[/bold]\n"
            f"[muted]\u2191 +{hidden} chars "
            f"(ctrl+e to expand)[/muted]\n"
            f"{text[-_DISPLAY_CHARS:]}"
        )


__all__ = ["StreamingText"]
