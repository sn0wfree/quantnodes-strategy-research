"""ChatSession — orchestrates the per-turn input → dispatch loop.

The session is a thin layer over :func:`cli.interactive.main.process_turn`
that already implements:

* halt/resume intercept
* mandate pick intercept
* slash command dispatch (via :data:`_DISPATCH`)
* plain-text user turn append

The session wraps each of those effects with a thin Textual-aware
shell:

* user input is read from ``ctx.pending_input`` (set by :class:`ResumeOrNewModal`
  on accept) or arrived via :class:`SynthesizeInput` from
  :class:`ChatInput` / :class:`CommandSidebar`.
* the session calls ``process_turn(text, ctx)`` and inspects the rc.
* on ``rc == 2`` (the ``/quit`` sentinel from ``cmd_quit``), the
  session asks the app to exit.
* on the session observing ``ctx.pending_prompt`` (used by
  ``/journal`` / ``/shadow`` queue-a-prompt), it re-enters
  immediately with that prompt so the user does not have to
  round-trip through stdin.

Handler output capture (Commit 3)
* Handlers still write to the singleton via ``console.print(...)``. In
  TUI mode we wrap each turn in :func:`cli.theme.captured_console` so
  :func:`cli.theme.get_console` returns a recording console for the
  duration. Anything the handler prints accumulates inside that
  console and is then forwarded to the app's TranscriptView.

LLM streaming (Commit 5)
* Plain-text turns (``rc == 0`` and ``process_turn`` has appended the
  user turn to ``ctx.history``) are routed through
  :mod:`cli.llm_streaming.stream_chat_to_tui` which posts token deltas
  to the TranscriptView and appends the final assistant message back to
  ``ctx.history``. No-op when an LLM client has not been installed
  (e.g. in tests); the session falls back to plain chat history.
"""
from __future__ import annotations

from typing import Any, Optional

from strategy_research.cli.halt import clear_halt as _clear_halt
from strategy_research.cli.halt import is_halted as _is_halted
from strategy_research.cli.halt import trip_halt as _trip_halt
from strategy_research.cli.interactive.main import process_turn
from strategy_research.cli.theme import captured_console
from strategy_research.cli.tui.messages import SynthesizeInput, WriteTranscript


# Standard ``cmd_quit`` sentinel. See ``cli/commands/slash_chat.py``.
QUIT_RC: int = 2


class ChatSession:
    """Single-turn dispatcher that wraps ``process_turn``.

    Holds a reference to the running :class:`ResearchApp` so it can
    request ``app.exit()`` on ``/quit`` and post messages back into
    the widget tree. Optionally holds an :class:`OpenAICompatClient` so
    plain-text turns can stream from the configured LLM into the
    TranscriptView.
    """

    def __init__(
        self,
        ctx: Any,
        *,
        app: Any = None,
        llm_client: Any = None,
        session_logger: Any = None,
        transcript_width: int = 120,
    ) -> None:
        self.ctx = ctx
        self.app = app
        self.transcript_width = transcript_width
        # Optional OpenAICompatClient. When set, plain-text turns are
        # streamed via ``stream_chat_to_tui``. When None, plain-text
        # turns append to history only.
        self.llm_client = llm_client
        # ``session_logger`` is an optional callable taking
        # ``(session_id, role, content)``. Reserved for Commit 5 (TTY
        # guard integration); unused here.
        self.session_logger = session_logger
        # Pending raw input buffer used by :meth:`ResumeOrNewModal`.
        self._pending_input: Optional[str] = None

    # ------------------------------------------------------------------ API

    def enqueue(self, text: str) -> None:
        """Queue text for the next :meth:`dispatch` cycle."""
        self._pending_input = text

    async def dispatch(self, text: str) -> int:
        """Run a single turn: ``process_turn(text, ctx)`` → return rc.

        On ``rc == QUIT_RC`` (=2 from ``/quit``), the session asks
        the app to exit (if bound). On the session observing
        ``ctx.pending_prompt`` after a slash turn (``/journal``,
        ``/shadow``), the queued prompt is dispatched recursively.
        On a successful plain-text turn (``rc == 0`` with ``llm_client``
        bound), the session routes the messages payload through the
        streaming bridge so the LLM reply reaches the TranscriptView.
        """
        rc, captured_text = self._dispatch_with_capture(text)

        # Forward captured handler output to the TUI transcript.
        stripped = captured_text.strip("\n").strip()
        if stripped:
            self._write_captured(stripped)

        # If plain text and an LLM client is bound, route the user
        # turn through the streaming bridge.
        if (
            rc == 0
            and self.llm_client is not None
            and self.app is not None
            and text.strip()
            and not text.strip().startswith("/")
            and not text.strip().startswith("停")
        ):
            from strategy_research.cli.llm_streaming import (
                _build_messages,
                stream_chat_to_tui,
            )
            try:
                messages = _build_messages(self.ctx)
                await stream_chat_to_tui(
                    self.llm_client, messages, app=self.app, ctx=self.ctx,
                )
            except Exception as exc:  # noqa: BLE001
                self._write_transcript(f"[red]LLM bridge error:[/red] {exc}")

        # Drain ``ctx.pending_prompt`` queued by slash handlers.
        queued = getattr(self.ctx, "pending_prompt", None)
        if queued:
            self.ctx.pending_prompt = ""
            return await self._drain(queued, depth=0, accumulator=rc)

        if rc == QUIT_RC and self.app is not None:
            self.app.exit()
        return rc

    async def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Textual message handler: forward widget submissions here."""
        await self.dispatch(message.text)

    # ------------------------------------------------------------------ helpers

    def _dispatch_with_capture(self, text: str) -> tuple[int, str]:
        """Run ``process_turn`` inside a captured console.

        Returns ``(rc, captured_text)``. ``captured_text`` is the raw
        text the handler emitted through ``get_console()`` (empty for
        plain text turns that only append to history).
        """
        text_str = (text or "").strip()
        with captured_console(width=self.transcript_width) as rec:
            rc = process_turn(text_str, self.ctx)
            captured = rec.export_text(clear=False, styles=False)
        return rc, captured

    async def _drain(self, prompt: str, *, depth: int, accumulator: int) -> int:
        """Run a queued prompt, allowing up to 8 levels of re-queueing."""
        if depth >= 8:
            self._write_transcript(
                "[warning]Prompt queue depth exceeded — discarding remaining.[/]"
            )
            return accumulator
        rc, captured = self._dispatch_with_capture(prompt)
        stripped = captured.strip("\n").strip()
        if stripped:
            self._write_captured(stripped)
        next_queued = getattr(self.ctx, "pending_prompt", None)
        if next_queued:
            self.ctx.pending_prompt = ""
            return await self._drain(next_queued, depth=depth + 1, accumulator=rc)
        if rc == QUIT_RC and self.app is not None:
            self.app.exit()
        return rc

    def _write_transcript(self, content: Any) -> None:
        """Post a transcript line via the app's widget, if available."""
        if self.app is not None:
            self.app.write_transcript(content)

    def _write_captured(self, captured_text: str) -> None:
        """Render the captured ANSI/marked-up text into the transcript."""
        self._write_transcript(captured_text)

    # ------------------------------------------------------------------ halt API

    def trip_halt(self, *, reason: str = "user explicit halt") -> None:
        """Trip the kill switch (Ctrl+C or Ctrl+C typed into chat)."""
        _trip_halt(reason=reason)
        self._write_transcript(
            f"[warning]halt tripped ({reason}) — long-running loops will exit on next checkpoint.[/]"
        )

    def clear_halt(self) -> None:
        """Clear the kill switch (via /resume keyword)."""
        was = _is_halted()
        _clear_halt()
        if was:
            self._write_transcript(
                "[success]halt cleared — long-running loops may now proceed.[/]"
            )


__all__ = [
    "ChatSession",
    "QUIT_RC",
    "SynthesizeInput",
    "WriteTranscript",
]
