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
"""
from __future__ import annotations

from typing import Any, List, Optional

from strategy_research.cli.halt import clear_halt as _clear_halt
from strategy_research.cli.halt import is_halted as _is_halted
from strategy_research.cli.halt import trip_halt as _trip_halt
from strategy_research.cli.interactive.main import process_turn
from strategy_research.cli.tui.messages import SynthesizeInput, WriteTranscript


# Standard ``cmd_quit`` sentinel. See ``cli/commands/slash_chat.py``.
QUIT_RC: int = 2


class ChatSession:
    """Single-turn dispatcher that wraps ``process_turn``.

    Holds a reference to the running :class:`ResearchApp` so it can
    request ``app.exit()`` on ``/quit`` and post messages back into
    the widget tree.
    """

    def __init__(
        self,
        ctx: Any,
        *,
        app: Any = None,
        session_logger: Any = None,
    ) -> None:
        self.ctx = ctx
        self.app = app
        # ``session_logger`` is an optional callable taking
        # ``(session_id, role, content)``. Reserved for Commit 5 (TTY
        # guard integration); unused here.
        self.session_logger = session_logger
        # Pending raw input buffer used by :meth:`ResumeOrNewModal`.
        self._pending_input: Optional[str] = None

    # ------------------------------------------------------------------ API

    def enqueue(self, text: str) -> None:
        """Queue text for the next :meth:`dispatch` cycle.

        Used by :class:`ResumeOrNewModal` to inject a "first message"
        into the loop without going through the input bar.
        """
        self._pending_input = text

    async def dispatch(self, text: str) -> int:
        """Run a single turn: ``process_turn(text, ctx)`` → return rc.

        On ``rc == QUIT_RC`` (=2 from ``/quit``), the session asks
        the app to exit (if bound). On the session observing
        ``ctx.pending_prompt`` after a slash turn (``/journal``,
        ``/shadow``), the queued prompt is dispatched recursively.
        """
        rc = process_turn(text, self.ctx)

        # Drain ``ctx.pending_prompt`` queued by slash handlers.
        queued = getattr(self.ctx, "pending_prompt", None)
        if queued:
            self.ctx.pending_prompt = ""
            # Recursive drain — but we shouldn't recurse forever; cap at
            # 8 levels (way more than any realistic handler chain).
            return await self._drain(queued, depth=0, accumulator=rc)

        if rc == QUIT_RC and self.app is not None:
            self.app.exit()
        return rc

    async def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Textual message handler: forward widget submissions here."""
        await self.dispatch(message.text)

    # ------------------------------------------------------------------ helpers

    async def _drain(self, prompt: str, *, depth: int, accumulator: int) -> int:
        """Run a queued prompt, allowing up to 8 levels of re-queueing."""
        if depth >= 8:
            self._write_transcript(
                "[warning]Prompt queue depth exceeded — discarding remaining.[/]"
            )
            return accumulator
        rc = process_turn(prompt, self.ctx)
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
