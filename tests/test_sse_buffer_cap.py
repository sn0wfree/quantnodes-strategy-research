"""Tests for SSE buffer first-connect replay cap and other buffer behaviors."""

from __future__ import annotations


class TestSSEBufferReplayCap:
    def test_first_connect_capped_at_200(self):
        """get_events_since with empty last_id returns at most 200 events."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=5000)
        session_id = "sess-cap-1"

        # Push 300 events
        for i in range(300):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        events = buf.get_events_since(session_id, "")
        assert len(events) == 200
        # Should be the LAST 200 events (most recent)
        assert events[0].event == "event_100"
        assert events[-1].event == "event_299"

    def test_under_cap_returns_all(self):
        """Fewer than 200 events → returns all."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=5000)
        session_id = "sess-cap-2"

        for i in range(50):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        events = buf.get_events_since(session_id, "")
        assert len(events) == 50

    def test_with_last_id_no_cap(self):
        """get_events_since with a valid last_id returns all events after it (no cap)."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=5000)
        session_id = "sess-cap-3"

        # Push 250 events
        for i in range(250):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        # Access internal buffer to find an event id
        all_events = [e for e in buf._buffer if e.session_id == session_id]
        assert len(all_events) == 250

        # Use the 100th event's id as last_id
        mid_id = all_events[100].id
        events_after = buf.get_events_since(session_id, mid_id)
        assert len(events_after) == 149  # indices 101..249 = 149 events
