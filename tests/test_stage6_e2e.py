"""E2E test for Stage 6 - full conversation flow simulation.

Simulates a complete agent turn: iter_start -> thinking -> text_delta
streaming -> iter_end -> Done marker, then verifies the TUI state.
"""
from __future__ import annotations

from unittest import mock

import pytest

from strategy_research.cli.tui.app import ResearchApp


class TestE2EConversationFlow:
    """Simulate a full agent turn through route_agent_event."""

    def _app(self) -> ResearchApp:
        return ResearchApp(skip_resume=True)

    def test_full_turn_lifecycle(self):
        """iter_start -> thinking_start -> text_delta x3 -> iter_end."""
        app = self._app()

        # Mock all widget interactions
        mock_thinking = mock.MagicMock()
        mock_streaming = mock.MagicMock()
        mock_rail = mock.MagicMock()
        mock_header = mock.MagicMock()
        mock_transcript = mock.MagicMock()

        with mock.patch.object(app, "start_thinking", mock_thinking), \
             mock.patch.object(app, "stop_thinking", mock.MagicMock()), \
             mock.patch.object(app, "update_streaming_delta", mock_streaming), \
             mock.patch.object(app, "query_one", side_effect=lambda cls: {
                 mock.MagicMock(): mock_rail,  # fallback
             }.get(cls, mock_transcript)), \
             mock.patch.object(app, "update_header", mock_header):

            # 1. iter_start
            app.route_agent_event("iter_start", {"iteration": 1, "max_iterations": 1})
            assert mock_thinking.called
            assert mock_header.called  # iter count updated

            # 2. thinking_start
            app.route_agent_event("thinking_start", {})

            # 3. text_delta x3 (streaming)
            app.route_agent_event("text_delta", {"text": "Hello"})
            app.route_agent_event("text_delta", {"text": " world"})
            app.route_agent_event("text_delta", {"text": "!"})
            assert mock_streaming.call_count == 3

            # 4. iter_end
            app.route_agent_event("iter_end", {"iteration": 1})

    def test_tool_call_result_flow(self):
        """tool_call -> tool_progress -> tool_result."""
        app = self._app()

        mock_rail = mock.MagicMock()
        with mock.patch.object(app, "query_one", return_value=mock_rail):
            # tool_call
            app.route_agent_event("tool_call", {
                "tool": "read_file",
                "arguments": {"path": "/x"},
                "iter": 1,
            })
            assert mock_rail.handle_event.call_count >= 1

            # tool_progress
            app.route_agent_event("tool_progress", {
                "tool": "read_file",
                "message": "reading...",
            })
            assert mock_rail.handle_event.call_count >= 2

            # tool_result
            app.route_agent_event("tool_result", {
                "tool": "read_file",
                "status": "ok",
                "elapsed_ms": 100,
            })
            assert mock_rail.handle_event.call_count >= 3

    def test_compact_flow(self):
        """compact event adds timeline entry."""
        app = self._app()

        mock_rail = mock.MagicMock()
        with mock.patch.object(app, "query_one", return_value=mock_rail):
            app.route_agent_event("compact", {
                "layer": "microcompact",
                "iteration": 1,
                "before_tokens": 12000,
                "after_tokens": 4000,
            })
            # write_rail forwards to handle_event
            calls = mock_rail.handle_event.call_args_list
            assert any(c.args[0] == "compact" for c in calls)

    def test_error_flow(self):
        """error event posts to transcript."""
        app = self._app()

        mock_transcript = mock.MagicMock()
        with mock.patch.object(app, "query_one", return_value=mock_transcript):
            app.route_agent_event("error", {"message": "boom", "fatal": True})
            # Should post a WriteTranscript message
            assert mock_transcript.post_message.called

    def test_unknown_event_no_crash(self):
        """Unknown event types are silently ignored."""
        app = self._app()
        app.route_agent_event("totally_unknown", {"x": 1})
        # No crash, no assertion needed
