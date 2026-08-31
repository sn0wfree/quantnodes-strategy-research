"""SSE Bridge v2 tests — EventStore → SSEEventBuffer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.event_store import EventStore
from strategy_research.core.agent.cache import CacheConfig


@pytest.fixture
def store(tmp_path):
    # EventStoreFactory removed — direct construction used instead
    s = EventStore(
        db_path=tmp_path / "bridge_test.db",
        cache_config=CacheConfig(min_entries=10, max_entries=100),
    )
    yield s
    try:
        s._backend._conn = None
    except Exception:
        pass


class TestBridgeV2:
    def test_bridge_sets_attached_flag(self, store):
        """Bridge sets _sse_bridge_attached flag."""
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            assert store._sse_bridge_attached is True

    def test_bridge_idempotent(self, store):
        """Calling attach twice should not double-wrap."""
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            attach_eventstore_to_sse(store)  # second call should be no-op
            # Should still be attached once
            assert store._sse_bridge_attached is True

    def test_bridge_preserves_original_pusher(self, store):
        """Original pusher is still called after wrapping."""
        received = []
        store._sse_pusher = lambda sid, ev: received.append((sid, ev))
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            store.emit("s1", "test.event", {})
            # Original pusher was called
            assert len(received) == 1

    def test_bridge_handles_sse_buffer_push_failure(self, store):
        """sse_buffer.push failure is swallowed."""
        mock_sse = MagicMock()
        mock_sse.push.side_effect = RuntimeError("push failed")
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            # Should not raise
            store.emit("s1", "test.event", {})

    def test_bridge_handles_original_pusher_failure(self, store):
        """Original pusher failure is swallowed."""
        store._sse_pusher = MagicMock(side_effect=RuntimeError("original failed"))
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            # Should not raise
            store.emit("s1", "test.event", {})

    def test_bridge_passes_event_type_and_data(self, store):
        """Bridge passes correct event type and data to sse_buffer.push."""
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            store.emit("s1", "my.event.type", {"my": "data"})
            mock_sse.push.assert_called_once()
            call_args = mock_sse.push.call_args
            assert call_args[0][0] == "my.event.type"
            assert call_args[0][2] == "s1"

    def test_bridge_no_original_pusher(self, store):
        """Bridge works when no original pusher is set."""
        store._sse_pusher = None
        mock_sse = MagicMock()
        with patch.dict("sys.modules", {"strategy_research.api.sse_buffer": MagicMock(sse_buffer=mock_sse)}):
            from strategy_research.api.session.bridge_v2 import attach_eventstore_to_sse
            attach_eventstore_to_sse(store)
            store.emit("s1", "test.event", {})
            mock_sse.push.assert_called_once()
