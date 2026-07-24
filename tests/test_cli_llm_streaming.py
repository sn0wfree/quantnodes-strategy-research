"""Tests for ``cli.llm_streaming`` — the LLM → Textual TUI bridge.

We don't hit the network in unit tests; instead we use a stub client
that yields :class:`StreamChunk` objects whose ``delta_content`` is
already-populated.
"""
from __future__ import annotations

from typing import Iterable, List
from unittest import mock

import pytest

from strategy_research.cli.llm_streaming import (
    _build_messages,
    _consume_sync_stream,
    stream_chat_to_tui,
)
from strategy_research.cli.tui.session import ChatSession
from strategy_research.core.llm.errors import LLMError, LLMTimeoutError
from strategy_research.core.llm.parser import StreamChunk


def _chunk(content: str, *, finish: str | None = None) -> StreamChunk:
    return StreamChunk(delta_content=content, finish_reason=finish)


class _FakeClient:
    """Mimics :class:`OpenAICompatClient.stream` yielding deltas.

    Optionally supports ``astream`` for the async tests.
    """

    def __init__(self, chunks: Iterable[StreamChunk] | None = None, *, error: Exception | None = None):
        self._chunks = list(chunks or [])
        self._error = error

    def stream(self, messages, **kw):
        if self._error is not None:
            raise self._error
        for c in self._chunks:
            yield c


# ─── _build_messages ────────────────────────────────────────────────


class TestBuildMessages:
    def test_empty_history_returns_empty(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=list)

        assert _build_messages(_Ctx()) == []

    def test_drops_empty_strings(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user", "content": ""},
                {"role": "user", "content": "ok"},
            ])

        msgs = _build_messages(_Ctx())
        assert msgs == [{"role": "user", "content": "ok"}]

    def test_filters_unknown_roles(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "you are a bot"},
                {"role": "tool", "content": "data"},
                {"role": "unknown_role", "content": "skip"},
                {"role": "assistant", "content": "hello"},
            ])

        msgs = _build_messages(_Ctx())
        assert msgs == [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "you are a bot"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_truncates_to_recent_12_turns(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
                for i in range(40)
            ])

        msgs = _build_messages(_Ctx())
        # The function takes ``[-12:]`` from history → last 12 (which is
        # still well above the off-screen noise floor; we test for "no
        # more than 12").
        assert len(msgs) <= 12
        # And the most recent turn survives.
        assert msgs[-1]["content"] == "msg39"


# ─── _consume_sync_stream ───────────────────────────────────────────


class TestConsumeSyncStream:
    def test_folds_deltas_into_one_string(self):
        client = _FakeClient([
            _chunk("Hello"),
            _chunk(", "),
            _chunk("world!"),
        ])
        text, char_count = _consume_sync_stream(client, [])
        assert text == "Hello, world!"
        assert char_count == 13

    def test_empty_stream_returns_empty(self):
        client = _FakeClient([])
        text, char_count = _consume_sync_stream(client, [])
        assert text == ""
        assert char_count == 0

    def test_propagates_llm_error(self):
        client = _FakeClient(error=LLMTimeoutError("nope"))
        with pytest.raises(LLMTimeoutError):
            _consume_sync_stream(client, [])


# ─── stream_chat_to_tui ─────────────────────────────────────────────


class _FakeApp:
    """Minimal Textual app stub that collects WriteTranscript posts.

    Mirrors :meth:`ResearchApp.write_transcript`'s contract — resolves
    the TranscriptView via ``query_one`` and forwards each message to
    its :meth:`on_write_transcript` handler (which we shim). This
    matches what happens in a real :class:`ResearchApp` mount cycle.
    """

    def __init__(self) -> None:
        self.writes: list = []
        self.exit_count = 0
        self._tv = _FakeTranscriptViewSink(self)

    def query_one(self, *_args, **_kw):
        return self._tv

    def post_message(self, message) -> None:
        # App-level post_message is not used by the streaming bridge
        # (we route through query_one); we keep this for compatibility.
        self.writes.append(message)


class _FakeTranscriptViewSink:
    """Stand-in for the :class:`TranscriptView` widget.

    The bridge calls ``app.query_one(TranscriptView).post_message(...)``
    so we forward every ``WriteTranscript`` to the parent app's
    ``writes`` collection.
    """

    def __init__(self, app: _FakeApp) -> None:
        self._app = app

    def post_message(self, message) -> None:
        self._app.writes.append(message)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_writes_thinking_and_final():
    app = _FakeApp()
    client = _FakeClient([
        _chunk("Hello"),
        _chunk(", world"),
    ])
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    contents = [str(m.content) for m in app.writes]
    assert any("thinking" in c for c in contents)
    assert any("Hello, world" in c for c in contents)
    assert any("chars" in c for c in contents)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_renders_error_line_on_llm_failure():
    app = _FakeApp()
    client = _FakeClient(error=LLMError("rate limit exceeded"))
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 1
    assert any("rate limit exceeded" in str(m.content) for m in app.writes)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_appends_to_ctx_history():
    app = _FakeApp()
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    client = _FakeClient([_chunk("model reply")])
    rc = await stream_chat_to_tui(client, [], app=app, ctx=ctx)
    assert rc == 0
    # The assistant turn was appended.
    assert ctx.history == [{"role": "assistant", "content": "model reply"}]


@pytest.mark.asyncio
async def test_stream_chat_to_tui_empty_response_does_not_crash():
    app = _FakeApp()
    client = _FakeClient([])  # no chunks
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    # Should have written the thinking line plus an "(empty response)" hint.
    contents = [str(m.content) for m in app.writes]
    assert any("empty response" in c for c in contents)


# ─── ChatSession LLM integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_session_dispatches_plain_text_to_llm():
    """When ``llm_client`` is bound, plain-text turns go through the LLM bridge."""
    from strategy_research.cli.interactive.main import InteractiveContext
    from strategy_research.cli.tui.session import ChatSession
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)

        def post_message(self, message) -> None:
            self.writes.append(message)

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    app = _App()
    client = _FakeClient([_chunk("model reply here")])
    s = ChatSession(ctx, app=app, llm_client=client)
    rc = await s.dispatch("hi there")
    assert rc == 0
    # Assistant reply appended.
    assert any(t.get("role") == "assistant" for t in ctx.history)


@pytest.mark.asyncio
async def test_chat_session_skips_llm_for_slash_commands():
    """Slash commands go through _DISPATCH only, not the LLM bridge."""
    from strategy_research.cli.interactive.main import InteractiveContext
    from strategy_research.cli.tui.session import ChatSession
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)
        exit_called: bool = False

        def post_message(self, message) -> None:
            self.writes.append(message)

        def exit(self) -> None:
            self.exit_called = True

        def write_transcript(self, content) -> None:
            self.writes.append(content)

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)
        debug: bool = False
        pending_prompt: str = ""
        session_id: str = "cli"

    ctx = _Ctx()
    app = _App()
    # A client that would explode if invoked.
    client = mock.MagicMock()
    client.stream.side_effect = AssertionError("stream should NOT be invoked for slash commands")
    s = ChatSession(ctx, app=app, llm_client=client)
    rc = await s.dispatch("/help")
    assert rc == 0
    # Mock client.stream was never called.
    client.stream.assert_not_called()
    # App exited (rc == 0 means /help ran — it does NOT exit). It only
    # exits on /quit with rc=2. So app.exit_called must be False.
    assert not app.exit_called


@pytest.mark.asyncio
async def test_chat_session_no_client_no_llm_call():
    """When ``llm_client is None`` plain text still appends to history only."""
    from strategy_research.cli.interactive.main import InteractiveContext
    from strategy_research.cli.tui.session import ChatSession
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)

        def post_message(self, message) -> None:
            self.writes.append(message)

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    app = _App()
    s = ChatSession(ctx, app=app, llm_client=None)  # no LLM
    rc = await s.dispatch("hi")
    assert rc == 0
    # No LLM call, but user turn landed in history.
    assert ctx.history == [{"role": "user", "content": "hi"}]
    # No transcripts were posted.
    assert app.writes == []
