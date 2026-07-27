"""Tests for route_agent_event think-tag stripping.

Verifies that ``text_delta`` and ``assistant_message`` events routed
through ``ResearchApp.route_agent_event`` have thinking/reasoning
tags stripped before reaching the TranscriptView.
"""
from __future__ import annotations

from unittest import mock

import pytest

from strategy_research.cli.tui.app import ResearchApp
from strategy_research.cli.tui.widgets.transcript import TranscriptView


class TestTextDeltaStrip:
    """text_delta events get thinking tags stripped."""

    def _app_with_fake_tv(self) -> tuple[ResearchApp, mock.MagicMock, mock.MagicMock]:
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        mock_tv = mock.MagicMock(spec=TranscriptView)
        app.query_one = mock.MagicMock(return_value=mock_tv)
        # Mock update_streaming_delta explicitly
        app.update_streaming_delta = mock.MagicMock()
        return app, mock_tv, app.update_streaming_delta

    def test_strips_think_tag(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {
            "text": "<think>reasoning</think>visible answer",
        })
        # update_streaming_delta called with stripped text
        mock_usd.assert_called_once_with("visible answer")

    def test_strips_unclosed_think(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {
            "text": "before<think>still reasoning",
        })
        mock_usd.assert_called_once_with("before")

    def test_passes_plain_text_through(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {"text": "plain answer"})
        mock_usd.assert_called_once_with("plain answer")

    def test_empty_text_passes_through(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {"text": ""})
        mock_usd.assert_called_once_with("")


class TestAssistantMessageStrip:
    """assistant_message events get thinking tags stripped + Markdown render."""

    def _app_with_fake_tv(self):
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        mock_tv = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=mock_tv)
        return app, mock_tv

    def test_strips_and_writes_markdown(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {
            "content": "<think>internal</think># Visible\n\nMarkdown content",
        })
        # write_assistant_message called with stripped content
        mock_tv.write_assistant_message.assert_called_once_with("# Visible\n\nMarkdown content")

    def test_calls_write_assistant_message(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {"content": "answer"})
        mock_tv.write_assistant_message.assert_called_once_with("answer")

    def test_does_not_call_finalize_method(self):
        """The old _finalize_assistant_message helper is gone."""
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {"content": "x"})
        # The new flow goes through write_assistant_message on TV
        # (which writes Markdown) — NOT through a fold-end path.
        mock_tv.end_streaming.assert_not_called()

    def test_chinese_content_through_route(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {
            "content": "<think>推理</think># A股动量策略\n\n关键指标: 12.3%",
        })
        mock_tv.write_assistant_message.assert_called_once_with(
            "# A股动量策略\n\n关键指标: 12.3%"
        )


class TestStripSourceIsTextFilters:
    """Sanity: route_agent_event imports from text_filters, not inline regex."""

    def test_uses_text_filters_module(self):
        import inspect
        from strategy_research.cli.tui import app as app_module
        src = inspect.getsource(app_module.ResearchApp.route_agent_event)
        assert "strip_thinking_tags" in src
        assert "from strategy_research.cli.tui.text_filters import" in src