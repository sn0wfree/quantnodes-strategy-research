"""Tests for error message handling in SessionService.

Covers:
- _friendly_error_text helper maps error details to user-friendly messages
- _row_to_message builds text part for error messages
- Error message type is preserved through persist + load
"""

from __future__ import annotations

from strategy_research.api.session.service import _friendly_error_text


class TestFriendlyErrorText:
    def test_rate_limit(self):
        assert "频率过高" in _friendly_error_text("LLMRateLimitError: rate limited (429)")
        assert "频率过高" in _friendly_error_text("429 Too Many Requests")
        assert "频率过高" in _friendly_error_text("too many requests")

    def test_timeout(self):
        assert "超时" in _friendly_error_text("LLMTimeoutError: timed out after 60s")
        assert "超时" in _friendly_error_text("request timeout")

    def test_auth(self):
        assert "鉴权" in _friendly_error_text("LLMAuthError: auth failed (401)")
        assert "鉴权" in _friendly_error_text("403 forbidden")

    def test_quota(self):
        assert "配额" in _friendly_error_text("LLMQuotaError: quota exceeded")
        assert "配额" in _friendly_error_text("balance insufficient")

    def test_server_error(self):
        assert "不可用" in _friendly_error_text("LLMServerError: server error (500)")
        assert "不可用" in _friendly_error_text("502 bad gateway")
        assert "不可用" in _friendly_error_text("503 service unavailable")

    def test_unknown_error(self):
        result = _friendly_error_text("some random error")
        assert result.startswith("⚠️")
        assert "失败" in result

    def test_empty_input(self):
        result = _friendly_error_text("")
        assert result.startswith("⚠️")

    def test_none_input(self):
        result = _friendly_error_text(None)  # type: ignore
        assert result.startswith("⚠️")


class TestErrorMessagePersistence:
    def test_row_to_message_error_builds_text_part(self):
        """_row_to_message builds text part from content for error messages."""
        from strategy_research.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-1",
            "session_id": "sess-1",
            "role": "assistant",
            "content": "⚠️ 模型请求频率过高",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": '{"status": "error", "details": "LLMRateLimitError: 429"}',
            "message_type": "error",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "error"
        assert msg["role"] == "assistant"
        assert msg["content"] == "⚠️ 模型请求频率过高"
        assert msg["metadata"]["status"] == "error"
        assert msg["metadata"]["details"] == "LLMRateLimitError: 429"
        assert msg["parts"] is not None
        assert len(msg["parts"]) == 1
        assert msg["parts"][0]["type"] == "text"
        assert msg["parts"][0]["text"] == "⚠️ 模型请求频率过高"

    def test_row_to_message_user_still_builds_text_part(self):
        """Regression: user messages still get text parts from content."""
        from strategy_research.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-2",
            "session_id": "sess-1",
            "role": "user",
            "content": "hello world",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": None,
            "message_type": "user",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "user"
        assert msg["parts"] is not None
        assert len(msg["parts"]) == 1
        assert msg["parts"][0]["text"] == "hello world"

    def test_row_to_message_assistant_no_parts(self):
        """Assistant messages without parts stay empty (streaming fills them)."""
        from strategy_research.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-3",
            "session_id": "sess-1",
            "role": "assistant",
            "content": "some content",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": None,
            "message_type": "assistant",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "assistant"
        assert msg["parts"] is None or len(msg["parts"]) == 0
