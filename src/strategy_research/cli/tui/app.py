"""ResearchApp — top-level Textual application.

Composes:
* Header (title)
* CommandSidebar (left) — clickable slash command list
* TranscriptView (centre) — banner Renderable is the first row
* ActivityRail (right) — event ticker
* ChatInput (bottom) — submit posts ``SynthesizeInput``
* HintFooter (very bottom)

Session orchestration (Commit 2):
* :class:`ChatSession` wraps :func:`cli.interactive.main.process_turn`
  for input dispatch (halt/resume/mandate/slash/quit sentinel).
* Key bindings (Ctrl+C halt, Ctrl+D quit, F1 help, Ctrl+L clear)
  delegate to the session.

Public API:
* :func:`run_tui` (in ``cli/tui/__init__``) — entrypoint for tests
  and the dispatcher.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional

from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Header as TUIHeader

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.messages import (
    SynthesizeInput,
    WriteTranscript,
)
from strategy_research.cli.tui.session import ChatSession, QUIT_RC
from strategy_research.cli.tui.widgets import (
    ActivityRail,
    Banner,
    ChatInput,
    CommandSidebar,
    HintFooter,
    ResumeOrNewModal,
    TranscriptView,
)

# CSS_PATH is resolved relative to the file defining the App — Textual
# looks for a sibling ``.tcss`` at import time.
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
        skip_resume: bool = False,
        llm_client: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._version = version
        self._session_db_path = session_db_path
        # Per-session state shared with the legacy REPL (process_turn dispatcher).
        self.ctx = InteractiveContext()
        self.banner: Optional[Banner] = None
        self.session: Optional[ChatSession] = None
        # When True, skip the resume-or-new modal. Used by tests and
        # by the ``--repl`` legacy escape hatch when resuming a specific
        # session by id (handled in a later commit).
        self._skip_resume = skip_resume
        # Optional OpenAI-compat client for streaming plain-text turns.
        self._llm_client = llm_client

    def compose(self):
        yield TUIHeader(show_clock=False)
        with Horizontal(id="main-row"):
            yield CommandSidebar(id="sidebar")
            yield TranscriptView(id="transcript")
            yield ActivityRail(id="rail")
        yield ChatInput(id="input")
        yield HintFooter()

    # ------------------------------------------------------------------ on_mount

    async def on_mount(self) -> None:
        # 1) Banner Renderable sits as the first row inside the transcript.
        from strategy_research.cli.ui.banner import render_banner
        transcript = self.query_one(TranscriptView)
        self.banner = Banner(model=self._model, version=self._version, mode="tui")
        banner_text = render_banner(
            model=self._model, version=self._version, mode="tui"
        )
        transcript.write(banner_text)

        # 2) Construct the session and bind it to the dispatch surface.
        self.session = ChatSession(
            self.ctx, app=self, llm_client=self._llm_client,
        )

        # 3) Decide whether to push the resume modal.
        if self._skip_resume:
            return

        latest_title = self._probe_latest_session_title()
        # Only ask if a prior session exists. Otherwise auto-new.
        if latest_title is None:
            return

        # Push the modal; the callback wires the user's choice into the session.
        modal = ResumeOrNewModal(latest_session=latest_title)
        self.push_screen(modal, self._on_resume_choice)

    # ------------------------------------------------------------------ resume

    def _probe_latest_session_title(self) -> Optional[str]:
        """Return the most-recent persisted session's title, or None.

        Uses the existing ``SessionDB`` (``core/session/db.py``). Failures
        are swallowed and treated as "no prior session" — fresh start is
        always a valid choice.
        """
        try:
            from strategy_research.core.session.db import SessionDB
            db = SessionDB()
            sessions = db.list_sessions(workspace=None, limit=1)
        except Exception:
            return None
        if not sessions:
            return None
        title = getattr(sessions[0], "title", "") or "(untitled)"
        return title

    def _on_resume_choice(self, choice: tuple[bool, Optional[str]]) -> None:
        """Callback: take the (resume, pending_input) tuple."""
        if choice is None:
            return
        is_resume, pending_input = choice
        if self.session is None:
            return
        if is_resume:
            self._resume_most_recent_session()
        else:
            self._write_transcript("[muted]Started fresh session.[/muted]")
        if pending_input and self.session is not None:
            self.session.enqueue(pending_input)

    def _resume_most_recent_session(self) -> None:
        """Restore history + session_id from the most-recent persisted session."""
        try:
            from strategy_research.core.session.db import SessionDB
            db = SessionDB()
            sessions = db.list_sessions(workspace=None, limit=1)
        except Exception:
            self._write_transcript("[muted]Could not load prior session.[/muted]")
            return
        if not sessions:
            return
        sid = getattr(sessions[0], "session_id", None)
        title = getattr(sessions[0], "title", "(untitled)") or "(untitled)"
        if sid is None:
            return
        self.ctx.session_id = sid
        try:
            messages = db.get_messages(sid, limit=20)
            history = [
                {"role": m.role, "content": m.content}
                for m in messages
                if m.role in {"user", "assistant"} and (m.content or "").strip()
            ][-6:]
        except Exception:
            history = []
        self.ctx.history = history
        self._write_transcript(
            f"[muted]Resumed session: {title} ({len(history)} prior turns)[/muted]"
        )

    # ------------------------------------------------------------------ inbound

    def write_transcript(self, content: Any) -> None:
        """Forward a Renderable into the mounted TranscriptView."""
        try:
            transcript = self.query_one(TranscriptView)
        except Exception:
            return
        transcript.post_message(WriteTranscript(content=content))

    def write_rail(self, event: Any) -> None:
        """Forward an event into the mounted ActivityRail."""
        try:
            rail = self.query_one(ActivityRail)
        except Exception:
            return
        from strategy_research.cli.tui.messages import WriteRail
        rail.post_message(WriteRail(event=event))

    async def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Route ``ChatInput.Submitted`` / sidebar clicks to the session."""
        if self.session is None:
            return
        await self.session.on_synthesize_input(message)

    # ------------------------------------------------------------------ keybindings

    def action_halt(self) -> None:
        """Ctrl+C — trip the kill switch."""
        if self.session is None:
            return
        self.session.trip_halt(reason="ctrl+c")

    def action_quit_app(self) -> None:
        """Ctrl+D — leave the TUI cleanly."""
        self.exit()

    def action_resume(self) -> None:
        """``/resume``-style recovery — clear the kill switch."""
        if self.session is None:
            return
        self.session.clear_halt()

    def action_show_help(self) -> None:
        """F1 — render the help table into the transcript."""
        try:
            from strategy_research.cli.commands.help import render_help_table
            from rich.console import Console as RichConsole
            import io
            buf = io.StringIO()
            rc = render_help_table(console=RichConsole(file=buf, force_terminal=False))
            if buf.getvalue():
                self.write_transcript(buf.getvalue())
        except Exception:
            self.write_transcript("[muted]/help not yet rendered in TUI v1.[/muted]")

    def action_clear_transcript(self) -> None:
        """Ctrl+L — wipe the chat log."""
        try:
            tv = self.query_one(TranscriptView)
            tv.clear_log()
        except Exception:
            pass
        # Also wipe memory for the session so re-runs start clean.
        if self.session is not None:
            self.ctx.history = []


__all__ = ["ResearchApp"]
