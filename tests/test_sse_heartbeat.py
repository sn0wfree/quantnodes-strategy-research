"""Tests for SSE heartbeat behavior.

The heartbeat mechanism prevents the browser's EventSource from
reporting onerror on idle streams. The first yield of every
event_generator must be a comment line so StreamingResponse flushes
headers immediately, and subsequent heartbeats must be comment lines
so they don't pollute the frontend's switch statement.
"""

from __future__ import annotations

import pytest

from strategy_research.api.routers.chat import _heartbeat_sse


def test_heartbeat_returns_comment_line():
    """Heartbeat must be an SSE comment line (: prefix) so the browser
    ignores it without triggering onerror or onmessage."""
    out = _heartbeat_sse(1)
    assert out == ": heartbeat\n\n"
    assert out.startswith(":")
    # Comment lines ARE NOT a named event — no `event:` or `data:` fields.
    assert "event:" not in out
    assert "data:" not in out


def test_heartbeat_count_argument_ignored():
    """The count is a no-op for comment-line keep-alive; we keep it
    in the signature so the call site doesn't change. Ensure the
    output is stable regardless of count."""
    assert _heartbeat_sse(0) == _heartbeat_sse(999) == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_event_generator_yields_connected_first():
    """event_generator must yield ': connected\\n\\n' as its first
    chunk so FastAPI flushes response headers and the browser fires
    onopen without waiting for the first real event or the 15s
    heartbeat."""
    from unittest.mock import patch, MagicMock
    from fastapi import Request

    # Build a minimal request with a unique session id so register_session
    # returns a fresh asyncio.Event.
    mock_request = MagicMock(spec=Request)
    session_id = "test-sse-immediate-flush"

    # Patch out sse_buffer so the test doesn't need a real backend.
    fake_buffer = MagicMock()
    fake_buffer.register_session.return_value = MagicMock()
    fake_buffer.get_events_since.return_value = []
    fake_buffer.replay_from.return_value = []

    with patch("strategy_research.api.routers.chat.sse_buffer", fake_buffer), \
            patch("strategy_research.api.routers.web_session._fetch_session_owned") as mock_fetch:
        # Import inside the patch so the route module picks up the
        # mocked sse_buffer.
        from strategy_research.api.routers.chat import chat_events

        # Call the endpoint function directly. It returns a StreamingResponse
        # whose body is the event_generator coroutine.
        response = await chat_events(
            session_id=session_id,
            token=None,
            last_event_id=None,
            request=mock_request,
        )

        # Drain the generator — it must yield the comment line first.
        body_iter = response.body_iterator
        first_chunk = await body_iter.__anext__()
        assert first_chunk == ": connected\n\n"
