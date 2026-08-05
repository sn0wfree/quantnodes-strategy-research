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


class TestSSEBufferEvictionFallback:
    """Tests for the fallback behavior when last_id is evicted from the buffer."""

    def test_get_events_since_evicted_last_id_fallback(self):
        """When last_id is evicted, get_events_since returns recent events."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        # Small buffer: only 10 events fit
        buf = SSEEventBuffer(max_events=10, ttl_seconds=300)
        session_id = "sess-evict-1"

        # Push 5 events, record the first event's id
        for i in range(5):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)
        first_id = buf._buffer[0].id

        # Push 10 more — the first 5 (including first_id) are evicted
        for i in range(5, 15):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        # first_id is no longer in the buffer
        assert all(e.id != first_id for e in buf._buffer)

        # get_events_since with evicted last_id should fallback to recent events
        events = buf.get_events_since(session_id, first_id)
        assert len(events) > 0
        # Should return the most recent events (capped at 200)
        assert events[-1].event == "event_14"

    def test_replay_from_evicted_event_id_fallback(self):
        """When event_id is evicted, replay_from returns recent events."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=10, ttl_seconds=300)
        session_id = "sess-evict-2"

        for i in range(5):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)
        first_id = buf._buffer[0].id

        # Evict the first events
        for i in range(5, 15):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        # replay_from with evicted event_id should fallback
        events = buf.replay_from(first_id, session_id)
        assert len(events) > 0
        assert events[-1].event == "event_14"

    def test_get_events_since_valid_id_still_works(self):
        """When last_id exists in buffer, normal behavior is preserved."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=100, ttl_seconds=300)
        session_id = "sess-evict-3"

        for i in range(20):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        # Find a valid id in the middle
        all_events = [e for e in buf._buffer if e.session_id == session_id]
        mid_id = all_events[10].id

        events = buf.get_events_since(session_id, mid_id)
        # Should return events AFTER mid_id (not including mid_id)
        assert len(events) == 9
        assert events[0].event == "event_11"

    def test_replay_from_valid_id_still_works(self):
        """When event_id exists in buffer, normal behavior is preserved."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=100, ttl_seconds=300)
        session_id = "sess-evict-4"

        for i in range(20):
            buf.push(f"event_{i}", f'{{"n": {i}}}', session_id)

        all_events = [e for e in buf._buffer if e.session_id == session_id]
        mid_id = all_events[10].id

        events = buf.replay_from(mid_id, session_id)
        # Should return events AFTER mid_id (not including mid_id)
        assert len(events) == 9
        assert events[0].event == "event_11"

    def test_cross_session_eviction_does_not_crash(self):
        """Eviction from another session doesn't crash the fallback path."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=10, ttl_seconds=300)

        # Session A gets 5 events, then session B fills the buffer
        for i in range(5):
            buf.push(f"event_{i}", f'{{"n": {i}}}', "sess-A")
        first_id_a = buf._buffer[0].id

        for i in range(10):
            buf.push(f"event_{i}", f'{{"n": {i}}}', "sess-B")

        # Session A's first_id is evicted; all session A events are gone.
        # Fallback should not crash — it returns whatever remains (empty or not).
        events = buf.get_events_since("sess-A", first_id_a)
        assert isinstance(events, list)

    def test_partial_eviction_fallback_returns_surviving_events(self):
        """When some session events survive eviction, fallback returns them."""
        from strategy_research.api.sse_buffer import SSEEventBuffer

        buf = SSEEventBuffer(max_events=15, ttl_seconds=300)

        # Session A: 10 events
        for i in range(10):
            buf.push(f"event_A{i}", f'{{"n": "A{i}"}}', "sess-A")
        first_id_a = buf._buffer[0].id

        # Session B: 8 events → evicts first 5 from buffer (including some A events)
        for i in range(8):
            buf.push(f"event_B{i}", f'{{"n": "B{i}"}}', "sess-B")

        # Session A's first_id is evicted, but later A events survive
        events = buf.get_events_since("sess-A", first_id_a)
        assert len(events) > 0
        # The surviving events should all be from session A
        assert all(e.session_id == "sess-A" for e in events)
