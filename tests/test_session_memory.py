"""Tests for SessionMemoryHook (P2-b)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.hooks.bundled.session_memory import SessionMemoryHook
from strategy_research.core.hooks.context import AgentHookContext


class TestSessionMemoryHookBasic:
    """Basic SessionMemoryHook functionality."""

    def test_hook_name(self):
        hook = SessionMemoryHook()
        assert hook.name == "session_memory"

    def test_collects_messages_from_iteration(self):
        hook = SessionMemoryHook(messages_count=5)
        ctx = AgentHookContext(
            iteration=1,
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )
        hook.after_iteration(ctx)
        assert len(hook._pending_archive) == 2

    def test_collects_across_iterations(self):
        hook = SessionMemoryHook(messages_count=10)
        for i in range(3):
            ctx = AgentHookContext(
                iteration=i,
                messages=[{"role": "user", "content": f"msg_{i}"}],
            )
            hook.after_iteration(ctx)
        assert len(hook._pending_archive) == 3

    def test_respects_messages_count_limit(self):
        hook = SessionMemoryHook(messages_count=2)
        ctx = AgentHookContext(
            iteration=1,
            messages=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        )
        hook.after_iteration(ctx)
        assert len(hook._pending_archive) == 2

    def test_empty_messages_noop(self):
        hook = SessionMemoryHook()
        ctx = AgentHookContext(iteration=1, messages=[])
        hook.after_iteration(ctx)
        assert len(hook._pending_archive) == 0


class TestSessionMemoryArchive:
    """Test archive_session functionality."""

    def test_archive_writes_markdown_file(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path, messages_count=10)
        ctx = AgentHookContext(
            iteration=1,
            messages=[
                {"role": "user", "content": "test message"},
                {"role": "assistant", "content": "test response"},
            ],
        )
        hook.after_iteration(ctx)
        result = hook.archive_session(session_id="sess_001")

        assert result is not None
        assert result.exists()
        assert result.parent == tmp_path / "memory"
        content = result.read_text()
        assert "sess_001" in content
        assert "test message" in content
        assert "test response" in content

    def test_archive_clears_pending(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        assert len(hook._pending_archive) == 1

        hook.archive_session()
        assert len(hook._pending_archive) == 0

    def test_archive_no_messages_returns_none(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        result = hook.archive_session()
        assert result is None

    def test_archive_no_workspace_returns_none(self):
        hook = SessionMemoryHook(workspace=None)
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        result = hook.archive_session()
        assert result is None

    def test_archive_creates_memory_dir(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        hook.archive_session()
        assert (tmp_path / "memory").is_dir()

    def test_archive_filename_format(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        result = hook.archive_session()
        assert result is not None
        assert result.name.endswith("-session.md")

    def test_archive_truncates_long_content(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path, messages_count=10)
        long_msg = "x" * 1000
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": long_msg}],
        )
        hook.after_iteration(ctx)
        result = hook.archive_session()
        content = result.read_text()
        assert "..." in content  # truncated


class TestSessionMemoryWithSessionDB:
    """Test SessionMemoryHook with SessionDB integration (P2-b)."""

    def test_archive_writes_to_session_db(self, tmp_path):
        from strategy_research.core.session.db import SessionDB

        db_path = tmp_path / "test_sessions.db"
        db = SessionDB(db_path)
        session = db.create_session("sess_db_001", workspace=str(tmp_path))

        mock_manager = MagicMock()
        mock_manager.add_message = MagicMock()

        hook = SessionMemoryHook(
            workspace=tmp_path,
            messages_count=10,
            session_manager=mock_manager,
        )
        ctx = AgentHookContext(
            iteration=1,
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
        )
        hook.after_iteration(ctx)
        hook.archive_session(session_id="sess_db_001")

        assert mock_manager.add_message.call_count == 2
        mock_manager.add_message.assert_any_call(
            session_id="sess_db_001",
            role="user",
            content="hello",
        )
        mock_manager.add_message.assert_any_call(
            session_id="sess_db_001",
            role="assistant",
            content="world",
        )

    def test_archive_without_session_id_skips_db(self, tmp_path):
        mock_manager = MagicMock()
        hook = SessionMemoryHook(
            workspace=tmp_path,
            session_manager=mock_manager,
        )
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        hook.archive_session(session_id=None)

        mock_manager.add_message.assert_not_called()

    def test_archive_db_error_does_not_crash(self, tmp_path):
        mock_manager = MagicMock()
        mock_manager.add_message.side_effect = RuntimeError("DB error")

        hook = SessionMemoryHook(
            workspace=tmp_path,
            session_manager=mock_manager,
        )
        ctx = AgentHookContext(
            iteration=1,
            messages=[{"role": "user", "content": "x"}],
        )
        hook.after_iteration(ctx)
        # Should not raise
        result = hook.archive_session(session_id="sess_err")
        assert result is not None  # Markdown file still written


class TestSessionMemoryFormatting:
    """Test session formatting."""

    def test_format_session_with_session_id(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        content = hook._format_session("sess_123", messages)
        assert "sess_123" in content
        assert "user: q1" in content
        assert "assistant: a1" in content

    def test_format_session_without_session_id(self, tmp_path):
        hook = SessionMemoryHook(workspace=tmp_path)
        messages = [{"role": "user", "content": "test"}]
        content = hook._format_session(None, messages)
        assert "Session ID" not in content
        assert "user: test" in content
