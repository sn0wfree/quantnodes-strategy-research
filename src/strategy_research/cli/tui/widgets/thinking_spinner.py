"""ThinkingSpinner — inline spinner shown in TranscriptView while waiting for LLM.

Displays a brand-colored spinning indicator with elapsed time until the first
token arrives. Auto-hides when thinking_end event fires.
"""
from __future__ import annotations

import time
from typing import Any

from textual.widgets import Static

from strategy_research.cli.tui.theme import brand_tokens


class ThinkingSpinner(Static):
    """Inline spinner widget for thinking state."""

    DEFAULT_CSS = """
    ThinkingSpinner {
        height: 1;
        visibility: hidden;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._active = False
        self._start_time: float = 0.0
        self._timer: Any = None
        self._verb: str = "thinking"

    def start(self, verb: str = "thinking") -> None:
        """Start the spinner."""
        self._active = True
        self._verb = verb
        self._start_time = time.time()
        self.visible = True
        self._tick()
        if self._timer is None:
            self._timer = self.set_interval(0.1, self._tick)

    def stop(self) -> None:
        """Stop the spinner."""
        self._active = False
        self.visible = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _tick(self) -> None:
        """Update spinner display."""
        if not self._active:
            return
        elapsed = time.time() - self._start_time
        tokens = brand_tokens()
        self.update(
            f"[{tokens.primary}]{tokens.primary}[/] "
            f"[{tokens.muted}]{self._verb}... {elapsed:.1f}s[/{tokens.muted}]"
        )


__all__ = ["ThinkingSpinner"]
