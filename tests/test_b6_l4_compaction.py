"""B6: L4 auto-compaction event-sourced (Level 3, B6 commit 1).

Verifies that AgentLoop._persist_compaction_event:
1. Emits compact.ended event when event_bus is provided (webui path)
2. Falls back to direct persist_message when event_bus is None
   (legacy TUI/CLI path)
3. The emitted event triggers projector → messages table write
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import Projector


def _setup_db(db_path: Path) -> None:
    """Minimal schema for compaction tests."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            message_type TEXT NOT NULL DEFAULT 'assistant',
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL
        );
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "u", "test", 1.0, 1.0),
    )
    conn.commit()
    conn.close()


class TestCompactionMessageList(unittest.TestCase):
    """Test CompactionMessage.to_message_list() output format."""

    def test_to_message_list_basic(self) -> None:
        """Basic CompactionMessage → list[dict] for compact.ended event."""
        from strategy_research.core.agent.compaction_message import CompactionMessage
        comp = CompactionMessage(
            id="cmp_abc123def456",
            session_id="s1",
            summary="Test summary",
            recent="some recent text",
            reason="auto",
        )
        result = comp.to_message_list()

        self.assertEqual(len(result), 1)
        msg = result[0]
        self.assertEqual(msg["role"], "system")
        self.assertEqual(msg["content"], "Test summary")
        self.assertIn("compact-", msg["id"])

    def test_to_message_list_id_truncation(self) -> None:
        """Compact id is derived from the message id (truncated to 12 chars)."""
        from strategy_research.core.agent.compaction_message import CompactionMessage
        comp = CompactionMessage(
            id="cmp_very_long_uuid_string_that_exceeds_twelve_chars",
            session_id="s1",
            summary="s",
        )
        result = comp.to_message_list()
        # Format: "compact-" (8) + first 12 chars of id
        self.assertEqual(result[0]["id"], "compact-cmp_very_lon")
        # Length: 8 + 12 = 20
        self.assertEqual(len(result[0]["id"]), 20)

    def test_to_message_list_empty_summary(self) -> None:
        """Empty summary still produces a message (caller's responsibility to check)."""
        from strategy_research.core.agent.compaction_message import CompactionMessage
        comp = CompactionMessage(
            id="cmp_xyz",
            session_id="s1",
            summary="",
        )
        result = comp.to_message_list()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "")


class TestPersistCompactionEvent(unittest.TestCase):
    """Test AgentLoop._persist_compaction_event with event_bus."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.legacy_bus = EventBus()
        self.v2 = EventBusV2(self.legacy_bus, self.db_path, flush_to_messages=True)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _create_loop_with_bus(self) -> "AgentLoop":
        """Create AgentLoop with event_bus wired in (webui path)."""
        # Build a minimal LLMConfig to satisfy AgentLoop.__init__
        from strategy_research.core.agent.compact import CompactConfig
        from strategy_research.core.llm import LLMConfig
        from strategy_research.core.agent.loop import AgentLoop

        cfg = LLMConfig(model="test-model", base_url="http://localhost", api_key="test")
        loop = AgentLoop(
            config=cfg,
            registry=None,  # type: ignore
            session_id="s1",
            compact_config=CompactConfig(),
            event_bus=self.v2,
        )
        return loop

    def test_persist_with_event_bus_emits_compact_ended(self) -> None:
        """When event_bus is provided, _persist_compaction_event emits compact.ended."""
        loop = self._create_loop_with_bus()
        loop._persist_compaction_event(
            "L4 summary text",
            "recent messages context",
        )

        # Verify event was emitted to event_log
        self.assertGreater(self.v2.count("s1"), 0)
        events = self.v2.replay("s1")
        compact_events = [e for e in events if e.type == "compact.ended"]
        self.assertEqual(len(compact_events), 1)
        self.assertEqual(compact_events[0].data["summary"], "L4 summary text")
        self.assertEqual(compact_events[0].data["reason"], "auto")

    def test_persist_with_event_bus_writes_to_messages_table(self) -> None:
        """EventBusV2 → projector flushes → compaction message in messages table.

        L4 auto-compaction is a "compaction happened" marker, not a
        history replacement. So only 1 message is added (the marker).
        """
        loop = self._create_loop_with_bus()
        loop._persist_compaction_event(
            "Auto-compacted summary",
            "recent context",
        )

        # messages table should have 1 compaction message
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, content, message_type FROM messages "
            "WHERE session_id = ? ORDER BY seq",
            ("s1",),
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "system")
        self.assertEqual(rows[0]["content"], "Auto-compacted summary")
        self.assertEqual(rows[0]["message_type"], "compaction")

    def test_persist_skips_when_no_session_id(self) -> None:
        """No session_id → skip (no event emitted)."""
        from strategy_research.core.agent.compact import CompactConfig
        from strategy_research.core.llm import LLMConfig
        from strategy_research.core.agent.loop import AgentLoop

        cfg = LLMConfig(model="m", base_url="http://localhost", api_key="k")
        loop = AgentLoop(
            config=cfg,
            registry=None,  # type: ignore
            session_id=None,
            compact_config=CompactConfig(),
            event_bus=self.v2,
        )
        loop._persist_compaction_event("summary", "recent")
        # No event emitted
        self.assertEqual(self.v2.count("s1"), 0)

    def test_persist_skips_when_empty_summary(self) -> None:
        """Empty summary → skip."""
        loop = self._create_loop_with_bus()
        loop._persist_compaction_event("", "recent")
        loop._persist_compaction_event("   ", "recent")
        loop._persist_compaction_event("\n\n", "recent")
        # No event emitted (all 3 were empty)
        self.assertEqual(self.v2.count("s1"), 0)

    def test_persist_legacy_fallback_without_event_bus(self) -> None:
        """When event_bus is None, falls back to direct persist_message."""
        from unittest.mock import patch
        from strategy_research.core.agent.compact import CompactConfig
        from strategy_research.core.llm import LLMConfig
        from strategy_research.core.agent.loop import AgentLoop, compaction_persister_registered

        cfg = LLMConfig(model="m", base_url="http://localhost", api_key="k")
        loop = AgentLoop(
            config=cfg,
            registry=None,  # type: ignore
            session_id="s1",
            compact_config=CompactConfig(),
            event_bus=None,  # ← no event bus, legacy path
        )

        # Register the legacy persister (required now that we fail-fast
        # on missing registration), then mock the underlying call.
        with patch(
            "strategy_research.api.routers.web_session.persist_message"
        ) as mock_persist:
            with compaction_persister_registered(mock_persist):
                loop._persist_compaction_event("legacy summary", "recent")
            mock_persist.assert_called_once()

            call_kwargs = mock_persist.call_args.kwargs
            self.assertEqual(call_kwargs["session_id"], "s1")
            self.assertEqual(call_kwargs["content"], "legacy summary")
            self.assertEqual(call_kwargs["message_type"], "compaction")
            self.assertEqual(call_kwargs["role"], "assistant")  # DB compat

    def test_event_payload_includes_all_fields(self) -> None:
        """Event payload includes summary, reason, compaction_id, metadata."""
        loop = self._create_loop_with_bus()
        # Set up custom metadata
        loop._event_bus = self.v2

        # Patch CompactionMessage to include metadata
        from unittest.mock import patch
        from strategy_research.core.agent import compaction_message as cm_module

        original_new = cm_module.new_compaction_message
        def patched_new(session_id, summary, recent="", reason="auto", metadata=None):
            return original_new(
                session_id, summary, recent, reason,
                metadata={"compaction_reason": "overflow", "layer": "L4"},
            )
        with patch.object(cm_module, "new_compaction_message", patched_new):
            loop._persist_compaction_event("summary", "recent")

        # Verify payload
        events = self.v2.replay("s1")
        compact_evt = [e for e in events if e.type == "compact.ended"][0]
        # L4 emit does NOT include 'messages' (compaction is a marker,
        # not a history replacement)
        self.assertNotIn("messages", compact_evt.data)
        # These fields are present
        self.assertIn("summary", compact_evt.data)
        self.assertIn("compaction_id", compact_evt.data)
        self.assertIn("metadata", compact_evt.data)
        self.assertEqual(compact_evt.data["reason"], "auto")
        self.assertEqual(
            compact_evt.data["metadata"].get("compaction_reason"), "overflow",
        )


class TestL4CompactionIntegration(unittest.TestCase):
    """Integration: L4 compaction → event_log → projector → messages table."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.legacy_bus = EventBus()
        self.v2 = EventBusV2(self.legacy_bus, self.db_path, flush_to_messages=True)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_full_flow_with_existing_messages(self) -> None:
        """L4 compaction: existing messages + new compaction message via events.

        Existing messages must be in event_log (event-sourced world);
        directly inserted messages will be wiped by the next flush.
        So we pre-populate via events, not direct INSERT.
        """
        from strategy_research.core.agent.compact import CompactConfig
        from strategy_research.core.llm import LLMConfig
        from strategy_research.core.agent.loop import AgentLoop

        # Pre-populate event_log via events (mimics prior conversation)
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "first", "role": "user",
        })
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "first reply",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1", "text": "first reply",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "first reply",
        })

        # Create AgentLoop with event_bus
        cfg = LLMConfig(model="m", base_url="http://localhost", api_key="k")
        loop = AgentLoop(
            config=cfg,
            registry=None,  # type: ignore
            session_id="s1",
            compact_config=CompactConfig(),
            event_bus=self.v2,
        )

        # Trigger L4 compaction
        loop._persist_compaction_event("L4 summary", "recent")

        # Verify event_log has the events
        events = self.v2.replay("s1")
        self.assertGreater(len(events), 0)
        compact_events = [e for e in events if e.type == "compact.ended"]
        self.assertEqual(len(compact_events), 1)

        # Verify messages table has: u1, a1, compaction marker
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, message_type FROM messages WHERE session_id = ?",
            ("s1",),
        ).fetchall()
        conn.close()

        # 3 messages: u1 (user), a1 (assistant), compaction marker (system)
        self.assertEqual(len(rows), 3)
        roles = [r["role"] for r in rows]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertIn("system", roles)  # compaction marker
        msg_types = [r["message_type"] for r in rows]
        self.assertIn("compaction", msg_types)


if __name__ == "__main__":
    unittest.main()
