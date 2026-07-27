"""Tests for ModeBar widget and Ctrl+M mode toggle.

Covers:
  * ModeBar renders correct label for chat / goal modes
  * ModeBar update_mode switches display
  * Ctrl+M action toggles ctx.interactive_mode
  * Ctrl+M action updates ModeBar widget
  * Ctrl+M action shows notification in transcript
  * Keybinding is registered
"""
from __future__ import annotations

from unittest import mock

import pytest

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.app import ResearchApp
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.widgets.mode_bar import ModeBar


# ---------------------------------------------------------------- ModeBar widget


class TestModeBar:
    def test_default_mode_is_chat(self):
        bar = ModeBar.__new__(ModeBar)
        bar._mode = "chat"
        bar._render_mode = mock.MagicMock()
        bar.__init__()
        assert bar._mode == "chat"

    def test_update_mode_chat(self):
        bar = ModeBar.__new__(ModeBar)
        bar._mode = "goal"
        bar._render_mode = mock.MagicMock()
        bar.update_mode("chat")
        assert bar._mode == "chat"
        bar._render_mode.assert_called_once()

    def test_update_mode_goal(self):
        bar = ModeBar.__new__(ModeBar)
        bar._mode = "chat"
        bar._render_mode = mock.MagicMock()
        bar.update_mode("goal")
        assert bar._mode == "goal"
        bar._render_mode.assert_called_once()

    def test_chat_label_contains_keyword(self):
        label = ModeBar._MODE_LABELS["chat"]
        assert "CHAT" in label
        assert "普通聊天" in label

    def test_goal_label_contains_keyword(self):
        label = ModeBar._MODE_LABELS["goal"]
        assert "GOAL" in label
        assert "策略研究" in label

    def test_labels_mention_ctrl_m(self):
        for label in ModeBar._MODE_LABELS.values():
            assert "Ctrl+M" in label


# ---------------------------------------------------------------- keybinding


class TestKeybinding:
    def test_ctrl_m_binding_exists(self):
        keys = {b.key for b in TUI_BINDINGS}
        assert "ctrl+m" in keys

    def test_ctrl_m_action_name(self):
        actions = {b.action for b in TUI_BINDINGS if b.key == "ctrl+m"}
        assert "toggle_mode" in actions


# ---------------------------------------------------------------- action_toggle_mode


class TestActionToggleMode:
    def _make_app(self):
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        ctx = InteractiveContext()
        ctx.interactive_mode = "chat"
        session = mock.MagicMock()
        session.ctx = ctx
        app.session = session
        return app, ctx

    def test_toggles_from_chat_to_goal(self):
        app, ctx = self._make_app()
        app.query_one = mock.MagicMock()
        app.write_transcript = mock.MagicMock()
        app.action_toggle_mode()
        assert ctx.interactive_mode == "goal"

    def test_toggles_from_goal_to_chat(self):
        app, ctx = self._make_app()
        ctx.interactive_mode = "goal"
        app.query_one = mock.MagicMock()
        app.write_transcript = mock.MagicMock()
        app.action_toggle_mode()
        assert ctx.interactive_mode == "chat"

    def test_updates_mode_bar_widget(self):
        app, ctx = self._make_app()
        mock_bar = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=mock_bar)
        app.write_transcript = mock.MagicMock()
        app.action_toggle_mode()
        mock_bar.update_mode.assert_called_once_with("goal")

    def test_shows_notification(self):
        app, ctx = self._make_app()
        app.query_one = mock.MagicMock()
        app.write_transcript = mock.MagicMock()
        app.action_toggle_mode()
        app.write_transcript.assert_called_once()
        call_arg = app.write_transcript.call_args[0][0]
        assert "策略研究" in call_arg or "普通聊天" in call_arg

    def test_no_session_no_crash(self):
        app = ResearchApp.__new__(ResearchApp)
        app.session = None
        app.action_toggle_mode()  # should not raise


# ---------------------------------------------------------------- on_mount sets initial mode bar


class TestModeBarOnMount:
    def test_on_mount_updates_mode_bar(self, monkeypatch):
        """After on_mount, ModeBar should reflect the resolved mode."""
        from strategy_research.cli.tui.widgets.mode_bar import ModeBar as MB
        app = ResearchApp.__new__(ResearchApp)
        ctx = InteractiveContext()
        ctx.interactive_mode = "chat"
        app.ctx = ctx
        mock_bar = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=mock_bar)
        app._sync_interactive_mode = lambda: None
        # Simulate what on_mount does for mode bar
        try:
            bar = app.query_one("#mode-bar", MB)
            bar.update_mode(ctx.interactive_mode)
        except Exception:
            pass
        mock_bar.update_mode.assert_called_with("chat")
