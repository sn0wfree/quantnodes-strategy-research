"""ResearchApp — top-level Textual application.

Composes:
* Header (title)
* CommandSidebar (left)
* TranscriptView (centre) - mounted first row gets the Banner Renderable
* ActivityRail (right)
* ChatInput (bottom)
* HintFooter (very bottom)

Pure wiring in this commit — the session-level orchestration (input
dispatch, halt/resume intercept, mandate pick, LLM streaming) lands
in Commit 2 (ChatSession). For now, the input widget's submit handler
posts a synthetic message that the app simply echoes into the
transcript, so we can verify mounting + layout + theme.
"""
from __future__ import annotations

import os
from typing import Optional

from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Header as TUIHeader

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.messages import (
    SynthesizeInput,
    WriteTranscript,
)
from strategy_research.cli.tui.widgets import (
    ActivityRail,
    Banner,
    ChatInput,
    CommandSidebar,
    HintFooter,
    TranscriptView,
)

# CSS_PATH is relative to the file defining the App — Textual looks for
# sibling .tcss in the App's source directory at import time.
_HERE = os.path.dirname(os.path.abspath(__file__))


class ResearchApp(App):
    """Top-level Textual app for strategy-research."""

    CSS_PATH = os.path.join(_HERE, "styles.tcss")
    TITLE = "QuantNodes Strategy-Research"

    BINDINGS = list(TUI_BINDINGS)

    def __init__(
        self,
        *,
        model: str = "unknown",
        version: str = "0.4.0",
        session_db_path: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._version = version
        self._session_db_path = session_db_path
        # Per-session state shared with the legacy REPL (process_turn dispatcher).
        self.ctx = InteractiveContext()
        self.banner: Optional[Banner] = None

    def compose(self):
        yield TUIHeader(show_clock=False)
        with Horizontal(id="main-row"):
            yield CommandSidebar(id="sidebar")
            yield TranscriptView(id="transcript")
            yield ActivityRail(id="rail")
        yield ChatInput(id="input")
        yield HintFooter()

    def on_mount(self) -> None:
        # Banner sits as the first row inside the transcript (so it scrolls
        # away after the first launch but stays branded during a cold start).
        from strategy_research.cli.ui.banner import render_banner
        transcript = self.query_one(TranscriptView)
        self.banner = Banner(model=self._model, version=self._version, mode="tui")
        # Write the rendered Rich Text as the first transcript entry.
        banner_text = render_banner(
            model=self._model, version=self._version, mode="tui"
        )
        transcript.write(banner_text)

    def write_transcript(self, content) -> None:
        """Convenience helper for callers/tests.

        Forwards to the currently-mounted ``TranscriptView`` widget. The
        widget's ``on_write_transcript`` handler appends to its log.
        """
        try:
            transcript = self.query_one(TranscriptView)
        except Exception:
            return
        transcript.post_message(WriteTranscript(content=content))

    def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Synthesize-from-sidebar clicks route here.

        For Commit 1 we echo into the transcript. Commit 2 will wire
        this through :func:`cli.interactive.main.process_turn`.
        """
        # Side-effect-free echo so we can validate the wiring.
        self.write_transcript(f"[muted]echo:[/muted] {message.text}")


__all__ = ["ResearchApp"]
