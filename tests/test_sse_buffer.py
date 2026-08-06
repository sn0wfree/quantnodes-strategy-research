"""Tests for api/sse_buffer.py — ring buffer with replay + multicast wakeup."""

from __future__ import annotations

import asyncio
import time

import pytest

from strategy_research.api.sse_buffer import SSEEvent, SSEEventBuffer


# ────────────────────────── push / id ──────────────────────────


def test_push_returns_monotonic_ids():
    buf = SSEEventBuffer()
    id1 = buf.push("a", "{}", "s1")
    id2 = buf.push("b", "{}", "s1")
    assert id1.startswith("evt_")
    assert id2.startswith("evt_")
    assert int(id2.split("_")[1]) > int(id1.split("_")[1])


def test_push_stores_event_with_required_fields():
    buf = SSEEventBuffer()
    eid = buf.push("agent_start", '{"agent_id":"a"}', "s1")
    evts = buf.get_events_since("s1", "")
    assert len(evts) == 1
    e = evts[0]
    assert e.id == eid
    assert e.event == "agent_start"
    assert e.data == '{"agent_id":"a"}'
    assert e.session_id == "s1"
    assert e.timestamp > 0


# ────────────────────────── replay / get_events_since ──────────────────────────


def test_get_events_since_empty_returns_recent():
    buf = SSEEventBuffer()
    for i in range(3):
        buf.push("e", str(i), "s1")
    evts = buf.get_events_since("s1", "")
    assert [e.data for e in evts] == ["0", "1", "2"]


def test_get_events_since_caps_at_200():
    buf = SSEEventBuffer()
    for i in range(250):
        buf.push("e", str(i), "s1")
    evts = buf.get_events_since("s1", "")
    assert len(evts) == 200
    # Last 200 events, ordered oldest first.
    assert evts[0].data == "50"
    assert evts[-1].data == "249"


def test_get_events_since_filters_by_session():
    buf = SSEEventBuffer()
    buf.push("e", "1", "s1")
    buf.push("e", "2", "s2")
    buf.push("e", "3", "s1")
    s1 = [e.data for e in buf.get_events_since("s1", "")]
    s2 = [e.data for e in buf.get_events_since("s2", "")]
    assert s1 == ["1", "3"]
    assert s2 == ["2"]


def test_get_events_since_starts_after_last_id():
    buf = SSEEventBuffer()
    ids = [buf.push("e", str(i), "s1") for i in range(4)]
    # Replay starting AFTER ids[1] (i.e. "1") — should yield "2" and "3".
    evts = buf.get_events_since("s1", ids[1])
    assert [e.data for e in evts] == ["2", "3"]


def test_get_events_since_unknown_id_falls_back_to_recent():
    buf = SSEEventBuffer()
    buf.push("e", "1", "s1")
    buf.push("e", "2", "s1")
    evts = buf.get_events_since("s1", "evt_unknown")
    assert [e.data for e in evts] == ["1", "2"]


def test_replay_from_unknown_id_returns_recent():
    buf = SSEEventBuffer()
    buf.push("e", "1", "s1")
    buf.push("e", "2", "s1")
    evts = buf.replay_from("evt_unknown", "s1")
    assert [e.data for e in evts] == ["1", "2"]


def test_replay_from_known_id_starts_after():
    buf = SSEEventBuffer()
    ids = [buf.push("e", str(i), "s1") for i in range(3)]
    evts = buf.replay_from(ids[0], "s1")
    assert [e.data for e in evts] == ["1", "2"]


# ────────────────────────── ring buffer cap ──────────────────────────


def test_buffer_caps_at_max_events():
    buf = SSEEventBuffer(max_events=10)
    for i in range(25):
        buf.push("e", str(i), "s1")
    # Only the last 10 survive in the buffer (global, not per-session).
    evts = buf.get_events_since("s1", "")
    assert len(evts) == 10
    assert [e.data for e in evts] == [str(i) for i in range(15, 25)]


# ────────────────────────── TTL cleanup ──────────────────────────


def test_cleanup_removes_expired_events():
    buf = SSEEventBuffer(ttl_seconds=0.05)
    buf.push("e", "old", "s1")
    # Force the stored event's timestamp into the past.
    buf._buffer[0].timestamp = time.time() - 10
    buf.push("e", "new", "s1")  # triggers _cleanup
    assert [e.data for e in buf.get_events_since("s1", "")] == ["new"]


# ────────────────────────── multicast registration ──────────────────────────


@pytest.mark.asyncio
async def test_register_and_unregister_session():
    buf = SSEEventBuffer()
    evt = buf.register_session("s1")
    assert isinstance(evt, asyncio.Event)
    buf.unregister_session("s1", evt)
    # After unregister the session has no listeners.
    assert "s1" not in buf._session_events


@pytest.mark.asyncio
async def test_multiple_listeners_per_session_multicast():
    """push() notifies ALL listeners, not just one."""
    buf = SSEEventBuffer()
    evt1 = buf.register_session("s1")
    evt2 = buf.register_session("s1")
    assert len(buf._session_events["s1"]) == 2

    buf.push("e", "{}", "s1")
    assert evt1.is_set()
    assert evt2.is_set()


@pytest.mark.asyncio
async def test_unregister_removes_only_one_listener():
    buf = SSEEventBuffer()
    evt1 = buf.register_session("s1")
    evt2 = buf.register_session("s1")
    buf.unregister_session("s1", evt1)
    # Session still exists (evt2 is registered).
    assert "s1" in buf._session_events
    assert evt1 not in buf._session_events["s1"]


@pytest.mark.asyncio
async def test_unregister_last_listener_drops_session():
    buf = SSEEventBuffer()
    evt = buf.register_session("s1")
    buf.unregister_session("s1", evt)
    assert "s1" not in buf._session_events


def test_push_with_no_listeners_does_not_raise():
    """No listeners → no notification, no exception."""
    buf = SSEEventBuffer()
    buf.push("e", "{}", "unknown-session")


@pytest.mark.asyncio
async def test_push_wakes_waiting_listener():
    """A coroutine waiting on the registered event should wake on push()."""
    buf = SSEEventBuffer()
    evt = buf.register_session("s1")

    async def waiter():
        await asyncio.wait_for(evt.wait(), timeout=1)
        return "woken"

    async def notifier():
        await asyncio.sleep(0)
        buf.push("e", "{}", "s1")

    results = await asyncio.gather(waiter(), notifier())
    assert results[0] == "woken"