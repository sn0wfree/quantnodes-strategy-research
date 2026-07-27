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
from typing import Any, Optional

from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Header as TUIHeader

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.messages import (
    SynthesizeInput,
    WriteTranscript,
)
from strategy_research.cli.tui.session import ChatSession
from strategy_research.cli.tui.widgets import (
    ActivityRail,
    Banner,
    ChatInput,
    CommandSidebar,
    HintFooter,
    Milestone,
    ResumeOrNewModal,
    StatusHeader,
    ThinkingSpinner,
    TimelineEntry,
    ToolsRail,
    TranscriptView,
)

# CSS_PATH is resolved relative to the file defining the App — Textual
# looks for a sibling ``.tcss`` at import time.
_HERE = os.path.dirname(os.path.abspath(__file__))


class ResearchApp(App):
    """Top-level Textual app for QuantNodes-Research."""

    CSS_PATH = os.path.join(_HERE, "styles.tcss")
    TITLE = "QuantNodes Strategy-Research"

    BINDINGS = list(TUI_BINDINGS)

    def __init__(
        self,
        *,
        model: str = "unknown",
        version: str = "0.4.2",
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
        # Tool-call totals (used by StatusHeader). Updated in
        # ``_bump_tool_count`` when ``tool_call`` / ``tool_result`` events
        # arrive via ``route_agent_event``.
        self._tool_total: int = 0
        self._tool_ok: int = 0

    def compose(self):
        yield StatusHeader(id="status-header")
        with Horizontal(id="main-row"):
            yield CommandSidebar(id="sidebar", classes="hidden")
            yield TranscriptView(id="transcript")
            yield ToolsRail(id="rail")
        yield ThinkingSpinner(id="thinking-spinner")
        yield ChatInput(id="input")
        yield HintFooter()

    # ------------------------------------------------------------------ on_mount

    async def on_mount(self) -> None:
        # 0) Resolve model from LLMConfig eagerly so the banner + header
        #    show the real provider (e.g. "minimax-M3") instead of the
        #    "unknown" default. Failures fall back to "unknown" silently.
        from strategy_research.cli.ui.banner import render_banner
        try:
            from strategy_research.core.llm.config import LLMConfig
            cfg = LLMConfig.load()
            if cfg.model:
                self._model = cfg.model
        except Exception:
            pass

        # 0a) Generate a fresh session id at startup so the header shows
        #     a real "sid:cli-xxxxxxxx" instead of the bare "cli"
        #     default. We deliberately don't touch ctx.session_id if it
        #     was already set (e.g. by a test fixture or a future
        #     resume-from-disk path).
        if self.ctx.session_id == "cli":
            try:
                import uuid
                self.ctx.session_id = f"cli-{uuid.uuid4().hex[:8]}"
            except Exception:
                self.ctx.session_id = "cli-fallback"

        # 1) Banner Renderable sits as the first row inside the transcript.
        transcript = self.query_one(TranscriptView)
        self.banner = Banner(model=self._model, version=self._version, mode="tui")
        banner_text = render_banner(
            model=self._model, version=self._version, mode="tui"
        )
        transcript.write(banner_text)

        # 1a) Push the resolved model + session id into the header so it
        #     is correct from the very first frame (not only after the
        #     first user input which would lazily trigger
        #     ``_update_header_stats``).
        try:
            self.update_header(
                model=self._model,
                session_id=self.ctx.session_id,
                connection_status="idle",
            )
        except Exception:
            pass

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

    def _write_transcript(self, content: Any) -> None:
        """Internal alias used by ``on_mount`` resume helpers.

        Delegates to the public :meth:`write_transcript` so internal
        callers (``_on_resume_choice``, ``_resume_most_recent_session``)
        can post session-info lines without hitting ``AttributeError``.
        """
        self.write_transcript(content)

    def write_rail(self, event_type: str, data: dict) -> None:
        """Forward an agent event into the mounted ToolsRail."""
        try:
            rail = self.query_one(ToolsRail)
        except Exception:
            return
        rail.handle_event(event_type, data)

    def update_header(self, **kwargs: Any) -> None:
        """Update the StatusHeader with new values."""
        try:
            header = self.query_one(StatusHeader)
        except Exception:
            return
        header.update_status(**kwargs)

    def update_tools_rail_goal(self, **kwargs: Any) -> None:
        """Update the ToolsRail goal section."""
        try:
            rail = self.query_one(ToolsRail)
        except Exception:
            return
        rail.update_goal(**kwargs)

    def route_agent_event(self, event_type: str, data: dict) -> None:
        """Route AgentLoop events to the appropriate widget."""
        if event_type == "text_delta":
            # Strip <think> / <reasoning> / <thinking> / <|reasoning|>
            # etc. before forwarding to the streamer so the user never
            # sees the model's internal monologue during typing.
            from strategy_research.cli.tui.text_filters import strip_thinking_tags
            self.update_streaming_delta(strip_thinking_tags(data.get("text", "")))
        elif event_type == "thinking_done":
            # Transition marker: thinking → text. Streaming already started.
            pass
        elif event_type == "assistant_message":
            # Final assistant content (non-streaming path or stream close).
            # Extract think tags (preserved as a foldable section) and
            # render the remaining body as Markdown (no fold). Streaming
            # preview (text_delta) still strips think tags so the user
            # never sees internal reasoning during typing.
            from strategy_research.cli.tui.text_filters import extract_thinking_tags
            raw_content = data.get("content", "") or ""
            think_content, body_content = extract_thinking_tags(raw_content)
            try:
                tv = self.query_one(TranscriptView)
                # Step 1: render think content as a foldable section
                # (collapsed by default; Ctrl+E to expand).
                if think_content:
                    tv.append_thinking(think_content)
                # Step 2: render body as Markdown, replacing the
                # streaming preview in-place.
                tv.write_assistant_message(body_content)
            except Exception:
                pass
        elif event_type in ("tool_call", "tool_result", "tool_progress", "tool_heartbeat"):
            # Stage C: tool calls are rendered inline in the transcript,
            # not the side rail. See TranscriptView.append_tool_call /
            # update_tool_result.
            self._route_tool_event(event_type, data)
        elif event_type == "compact":
            self.write_rail("compact", data)
        elif event_type == "llm_usage":
            self.update_header(token_used=data.get("output_tokens", 0))
        elif event_type == "iter_start":
            self.start_thinking()
            self.update_header(
                iter_count=data.get("iteration", 0),
                iter_max=data.get("max_iterations", 0),
            )
            try:
                rail = self.query_one(ToolsRail)
                rail.set_iter(data.get("iteration", 0), data.get("max_iterations", 0))
            except Exception:
                pass
        elif event_type == "iter_end":
            self.stop_thinking()
            try:
                tv = self.query_one(TranscriptView)
                tv.append_done()
            except Exception:
                pass
        elif event_type == "thinking_start":
            self.start_thinking()
        elif event_type == "thinking_end":
            self.stop_thinking()
        elif event_type == "error":
            try:
                tv = self.query_one(TranscriptView)
                from strategy_research.cli.tui.messages import WriteTranscript
                message = data.get("message", "unknown")
                if "quota" in message.lower():
                    friendly = "[yellow]\u26a0 MiniMax \u914d\u989d\u5df2\u7528\u5b8c\uff085\u5c0f\u65f6\u9650\u989d\uff09\u3002\u8bf7\u7a0d\u540e\u91cd\u8bd5\u6216\u5207\u6362 provider\u3002[/yellow]"
                else:
                    friendly = f"[red]Agent error:[/red] {message}"
                tv.post_message(WriteTranscript(content=friendly))
            except Exception:
                pass

    def _route_tool_event(self, event_type: str, data: dict) -> None:
        """Inline tool events into TranscriptView (stage C)."""
        try:
            tv = self.query_one(TranscriptView)
        except Exception:
            return
        if event_type == "tool_call":
            call_id = data.get("call_id", "")
            tool = data.get("tool", "?")
            args = data.get("args", {}) or {}
            tv.append_tool_call(call_id, tool, args)
            self._record_tool_start()
        elif event_type == "tool_result":
            call_id = data.get("call_id", "")
            ok = bool(data.get("ok", True))
            elapsed_ms = int(data.get("elapsed_ms", 0))
            tv.update_tool_result(call_id, ok, elapsed_ms)
            self._record_tool_result(ok=ok)

    def _record_tool_start(self) -> None:
        """Increment the tool-call counter and refresh the header."""
        try:
            self._tool_total += 1
            self.update_header(
                tool_count=self._tool_total,
                tool_ok=self._tool_ok,
            )
        except Exception:
            pass

    def _record_tool_result(self, *, ok: bool) -> None:
        """Increment the tool-success counter (only on success)."""
        try:
            if ok:
                self._tool_ok += 1
            self.update_header(
                tool_count=self._tool_total,
                tool_ok=self._tool_ok,
            )
        except Exception:
            pass

    def start_thinking(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.start()
        except Exception:
            pass

    def stop_thinking(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.stop()
        except Exception:
            pass

    def start_streaming(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.stop()
        except Exception:
            pass
        try:
            tv = self.query_one(TranscriptView)
            tv.begin_streaming()
        except Exception:
            pass

    def update_streaming(self, full_text: str) -> None:
        try:
            tv = self.query_one(TranscriptView)
            tv.update_streaming(full_text)
        except Exception:
            pass

    def update_streaming_delta(self, delta: str) -> None:
        """Accumulate a text_delta into the active streaming session."""
        try:
            tv = self.query_one(TranscriptView)
            if tv._streamer is None:
                # No active streaming session — start one for this delta
                tv.begin_streaming()
            tv._streamer.append_delta(delta)
            tv._truncate_to(tv._stream_baseline)
            rendered = tv._streamer.render()
            if rendered:
                tv.write(rendered)
        except Exception:
            pass

    def end_streaming(self, suffix: str = "") -> str:
        try:
            tv = self.query_one(TranscriptView)
            return tv.end_streaming(suffix=suffix)
        except Exception:
            return ""

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
            render_help_table(console=RichConsole(file=buf, force_terminal=False))
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

    def action_toggle_sidebar(self) -> None:
        """Ctrl+1 — toggle the Commands sidebar visibility."""
        try:
            sidebar = self.query_one("#sidebar")
            if "hidden" in sidebar.classes:
                sidebar.remove_class("hidden")
            else:
                sidebar.add_class("hidden")
        except Exception:
            pass

    def action_toggle_tools_rail(self) -> None:
        """Ctrl+2 - toggle the ToolsRail visibility."""
        try:
            rail = self.query_one("#rail")
            if "hidden" in rail.classes:
                rail.remove_class("hidden")
            else:
                rail.add_class("hidden")
        except Exception:
            pass

    def action_toggle_fold(self) -> None:
        """Ctrl+E - expand/collapse the most recent folded record."""
        try:
            tv = self.query_one(TranscriptView)
            tv.toggle_fold()
        except Exception:
            pass


__all__ = ["ResearchApp"]
