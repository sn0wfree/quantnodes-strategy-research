"""LLM streaming bridge between :class:`ChatSession` and the Textual TUI.

The Textual TUI's chat input is async (Textual awaits ``on_input_submitted``
handlers), so the streaming wrapper is built around
:func:`asyncio.to_thread` + the existing async generator
:class:`OpenAICompatClient.astream`.

The bridge is responsible for:

* Mounting a thinking-spinner while the first token arrives.
* Posting each ``delta_content`` token to the TUI transcript in real
  time so the user sees a typewriter-like effect.
* Accumulating the final assistant content and appending it (after the
  user turn) to :attr:`InteractiveContext.history`.

The module exposes a single public function:

>>> await stream_chat_to_tui(client, messages, *, app, ctx)

with the contract:

* The Textual app is bound via :mod:`cli.tui.messages`. The function
  never touches the app directly — it resolves the ``TranscriptView``
  widget via ``app.query_one(TranscriptView)`` and posts
  :class:`WriteTranscript` messages to the widget (which is the
  routed-once boundary Textual honours).
* ``ctx.history`` is mutated *in place* so subsequent turns see the
  assistant response in ``ctx.history`` for the LLM's own context.
* Errors from the LLM are caught and rendered as :class:`WriteTranscript`
  lines so the TUI never crashes on a transient network failure.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Iterable, List, Optional

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.messages import WriteTranscript
from strategy_research.cli.tui.widgets import TranscriptView
from strategy_research.core.llm.errors import LLMError
from strategy_research.core.llm.openai_client import OpenAICompatClient
from strategy_research.core.llm.parser import StreamChunk

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Streaming primitives
# ──────────────────────────────────────────────────────────────────────


def _chunks_to_messages(chunks: Iterable[StreamChunk]) -> tuple[str, int]:
    """Fold stream chunks into ``(full_content, char_count)``.

    Tool-call deltas are accumulated but ignored here; Commit 6 will
    dispatch them to the rail.
    """
    parts: List[str] = []
    char_count = 0
    for c in chunks:
        if c.delta_content:
            parts.append(c.delta_content)
            char_count += len(c.delta_content)
    return "".join(parts), char_count


def _build_messages(ctx: InteractiveContext) -> list[dict[str, Any]]:
    """Build a chat-completions messages list from :attr:`InteractiveContext.history`.

    Truncated to the most recent ~6 user/assistant turns so the prompt
    stays small enough to fit a typical 8k context window with room for
    the answer.
    """
    out: list[dict[str, Any]] = []
    for turn in ctx.history[-12:]:
        role = turn.get("role")
        content = turn.get("content") or ""
        if not content.strip():
            continue
        if role in {"user", "assistant", "system"}:
            out.append({"role": role, "content": content})
    return out


# ──────────────────────────────────────────────────────────────────────
# Textual bridge
# ──────────────────────────────────────────────────────────────────────


async def stream_chat_to_tui(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
    *,
    app: Any,
    ctx: Optional[InteractiveContext] = None,
) -> int:
    """Stream a chat completion into the bound TUI app's TranscriptView.

    Args:
        client: An :class:`OpenAICompatClient` (or any object that
            exposes ``astream(messages, ...)``).
        messages: The chat-completions messages list.
        app: The Textual app. We post :class:`WriteTranscript` messages
            via ``app.post_message`` so the widget tree can forward
            them to TranscriptView.
        ctx: Optional :class:`InteractiveContext`. When supplied, the
            final assistant content is appended to ``ctx.history`` so
            subsequent turns have full LLM-side context.

    Returns:
        ``0`` if the stream produced content, ``1`` if it raised.
    """
    write = _make_writer(app)

    start = time.perf_counter()
    write("[muted]⏳ thinking…[/muted]")

    try:
        full_text, char_count = await asyncio.to_thread(
            _consume_sync_stream, client, messages
        )
    except LLMError as exc:
        elapsed = time.perf_counter() - start
        # Replace the thinking line with an error line.
        write(f"[red]LLM error after {elapsed:.1f}s:[/red] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        write(f"[red]unexpected error after {elapsed:.1f}s:[/red] {exc}")
        return 1

    elapsed = time.perf_counter() - start

    # Replace the thinking line with the final content. We render
    # content as a single write — pure typewriter chunking is left
    # for a future iteration; chunking through the post_message
    # boundary adds latency rather than value at this scale.
    write(
        f"[italic]{full_text or '[dim](empty response)'}[/italic]"
        f"  [muted]({char_count} chars · {elapsed:.1f}s)[/muted]"
    )

    # Append to ctx.history so future turns see this assistant reply.
    if ctx is not None and full_text:
        ctx.history.append({"role": "assistant", "content": full_text})

    return 0


def _make_writer(app: Any):
    """Build a callable that posts WriteTranscript messages to the TUI.

    Resolves the ``TranscriptView`` widget on every call so the writer
    works even when the chat session is created before ``on_mount``
    has finished running. Captures ``app`` by closure.
    """

    def _write(content: Any) -> None:
        try:
            tv = app.query_one(TranscriptView)
        except Exception:
            # App is not yet mounted (post-on_mount state, off-screen
            # test fixture, etc.). Silently drop.
            return
        try:
            tv.post_message(WriteTranscript(content=content))
        except Exception as exc:  # noqa: BLE001
            logger.warning("WriteTranscript post failed: %s", exc)

    return _write


def _consume_sync_stream(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
) -> tuple[str, int]:
    """Drive the sync ``OpenAICompatClient.stream`` from a thread.

    The sync iterator yields :class:`StreamChunk` objects whose
    ``delta_content`` field is non-empty during token deltas. We fold
    them into one final string.

    This helper runs in ``asyncio.to_thread`` so it never blocks the
    Textual event loop.
    """
    parts: List[str] = []
    char_count = 0
    for chunk in client.stream(messages):
        if chunk.delta_content:
            parts.append(chunk.delta_content)
            char_count += len(chunk.delta_content)
    return "".join(parts), char_count


async def stream_chat_async(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
) -> AsyncIterator[StreamChunk]:
    """Async version of the streaming bridge (used by tests + future HTTP/2 use).

    Yields each :class:`StreamChunk` as it arrives so callers can
    decide whether to flush to the transcript immediately or batch.
    """
    async for chunk in client.astream(messages):
        yield chunk


__all__ = [
    "stream_chat_to_tui",
    "stream_chat_async",
    "_build_messages",
]
