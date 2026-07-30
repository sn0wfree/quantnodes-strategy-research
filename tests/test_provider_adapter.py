"""Tests for the provider adapter system.

Each provider has its own adapter that encapsulates:
- Default endpoint, model, max_tokens
- Thinking/reasoning token extraction
- Error handling
- HTTP customization
"""

from __future__ import annotations

import pytest

from strategy_research.core.llm.provider import (
    get_provider,
    get_provider_defaults,
    register_provider,
    ProviderAdapter,
    OpenAIAdapter,
    DeepSeekAdapter,
    MiniMaxAdapter,
    QwenAdapter,
    KimiAdapter,
    FallbackAdapter,
)


# ── Registry Tests ────────────────────────────────────────────────


class TestProviderRegistry:
    def test_get_known_providers(self):
        assert isinstance(get_provider("openai"), OpenAIAdapter)
        assert isinstance(get_provider("deepseek"), DeepSeekAdapter)
        assert isinstance(get_provider("minimax"), MiniMaxAdapter)
        assert isinstance(get_provider("qwen"), QwenAdapter)
        assert isinstance(get_provider("kimi"), KimiAdapter)

    def test_get_unknown_returns_fallback(self):
        assert isinstance(get_provider("unknown"), FallbackAdapter)

    def test_get_auto_returns_fallback(self):
        assert isinstance(get_provider("auto"), FallbackAdapter)

    def test_get_none_returns_fallback(self):
        assert isinstance(get_provider(None), FallbackAdapter)

    def test_get_empty_returns_fallback(self):
        assert isinstance(get_provider(""), FallbackAdapter)

    def test_register_custom_provider(self):
        class CustomAdapter(ProviderAdapter):
            @property
            def name(self) -> str:
                return "custom_test"

            @property
            def default_base_url(self) -> str:
                return "https://custom.test/v1"

            @property
            def default_model(self) -> str:
                return "custom-model"

            def extract_thinking_from_delta(self, delta):
                return None

            def extract_thinking_from_message(self, message):
                return None

        register_provider("custom_test", CustomAdapter)
        try:
            adapter = get_provider("custom_test")
            assert isinstance(adapter, CustomAdapter)
            assert adapter.name == "custom_test"
        finally:
            from strategy_research.core.llm.provider import _REGISTRY
            _REGISTRY.pop("custom_test", None)


# ── Provider Defaults Tests ─────────────────────────────────────


class TestProviderDefaults:
    def test_openai_defaults(self):
        defaults = get_provider_defaults("openai")
        assert defaults["base_url"] == "https://api.openai.com/v1"
        assert defaults["model"] == "gpt-4o-mini"
        assert defaults["max_tokens"] == 16384

    def test_deepseek_defaults(self):
        defaults = get_provider_defaults("deepseek")
        assert defaults["base_url"] == "https://api.deepseek.com/v1"
        assert defaults["model"] == "deepseek-chat"

    def test_minimax_defaults(self):
        defaults = get_provider_defaults("minimax")
        assert defaults["base_url"] == "https://api.minimaxi.com/v1"
        assert defaults["model"] == "minimax-M3"
        assert defaults["max_tokens"] == 32000

    def test_qwen_defaults(self):
        defaults = get_provider_defaults("qwen")
        assert "aliyuncs.com" in defaults["base_url"]

    def test_kimi_defaults(self):
        defaults = get_provider_defaults("kimi")
        assert "moonshot" in defaults["base_url"]


# ── Thinking Token Extraction Tests ─────────────────────────────


class TestThinkingExtraction:
    def test_deepseek_thinking_from_delta(self):
        adapter = DeepSeekAdapter()
        delta = {"reasoning_content": "I should think about this"}
        assert adapter.extract_thinking_from_delta(delta) == "I should think about this"

    def test_deepseek_thinking_normalized(self):
        adapter = DeepSeekAdapter()
        # normalize strips markdown (bold), not XML-style tags
        delta = {"reasoning_content": "**bold** text"}
        result = adapter.extract_thinking_from_delta(delta)
        assert result is not None
        assert "**" not in result
        assert result == "bold text"

    def test_deepseek_thinking_empty_delta(self):
        adapter = DeepSeekAdapter()
        assert adapter.extract_thinking_from_delta({}) is None
        assert adapter.extract_thinking_from_delta({"reasoning_content": ""}) is None

    def test_minimax_thinking_from_delta(self):
        adapter = MiniMaxAdapter()
        delta = {"content": "<think>plan</think>actual content"}
        assert adapter.extract_thinking_from_delta(delta) == "plan"

    def test_minimax_thinking_without_tags(self):
        adapter = MiniMaxAdapter()
        delta = {"content": "actual content"}
        assert adapter.extract_thinking_from_delta(delta) is None

    def test_minimax_thinking_from_message(self):
        adapter = MiniMaxAdapter()
        message = {"content": "<think>plan</think>final"}
        assert adapter.extract_thinking_from_message(message) == "plan"

    def test_openai_thinking_from_delta(self):
        adapter = OpenAIAdapter()
        delta = {"reasoning": "reasoning text"}
        assert adapter.extract_thinking_from_delta(delta) == "reasoning text"

    def test_openai_thinking_non_string_ignored(self):
        adapter = OpenAIAdapter()
        delta = {"reasoning": ["list", "not", "string"]}
        assert adapter.extract_thinking_from_delta(delta) is None

    def test_qwen_thinking_from_delta(self):
        adapter = QwenAdapter()
        delta = {"reasoning_content": "thinking"}
        assert adapter.extract_thinking_from_delta(delta) == "thinking"

    def test_kimi_thinking_never_present(self):
        adapter = KimiAdapter()
        assert adapter.extract_thinking_from_delta({"reasoning_content": "x"}) is None
        assert adapter.extract_thinking_from_message({"reasoning_content": "x"}) is None

    def test_fallback_thinking_never_present(self):
        adapter = FallbackAdapter()
        assert adapter.extract_thinking_from_delta({"reasoning_content": "x"}) is None
        assert adapter.extract_thinking_from_message({"reasoning_content": "x"}) is None


# ── Error Handling Tests ────────────────────────────────────────


class TestErrorHandling:
    def test_minimax_403_with_quota_error(self):
        from strategy_research.core.llm.errors import LLMQuotaError

        adapter = MiniMaxAdapter()
        body = {"error": {"code": "quota_exceeded"}}
        exc = adapter.handle_error(403, body)
        assert isinstance(exc, LLMQuotaError)
        assert "quota" in str(exc).lower()

    def test_minimax_403_with_billing_error(self):
        from strategy_research.core.llm.errors import LLMQuotaError

        adapter = MiniMaxAdapter()
        body = {"error": {"code": "billing_limit"}}
        exc = adapter.handle_error(403, body)
        assert isinstance(exc, LLMQuotaError)

    def test_minimax_403_without_quota_returns_none(self):
        adapter = MiniMaxAdapter()
        body = {"error": {"code": "invalid_api_key"}}
        assert adapter.handle_error(403, body) is None

    def test_minimax_429_quota_vs_rate_limit(self):
        from strategy_research.core.llm.errors import LLMQuotaError

        adapter = MiniMaxAdapter()
        quota_body = {"error": {"code": "quota_exceeded"}}
        exc = adapter.handle_error(429, quota_body)
        assert isinstance(exc, LLMQuotaError)

    def test_minimax_429_no_quota_returns_none(self):
        adapter = MiniMaxAdapter()
        assert adapter.handle_error(429, {}) is None

    def test_fallback_returns_none(self):
        adapter = FallbackAdapter()
        assert adapter.handle_error(403, {"error": {"code": "quota"}}) is None
        assert adapter.handle_error(429, {}) is None


# ── Error Code Extraction Tests ────────────────────────────────


class TestErrorCodeExtraction:
    def test_extract_from_error_dict(self):
        adapter = FallbackAdapter()
        body = {"error": {"code": "quota_exceeded"}}
        assert adapter.extract_error_code(body) == "quota_exceeded"

    def test_extract_from_top_level(self):
        adapter = FallbackAdapter()
        body = {"code": "rate_limit"}
        assert adapter.extract_error_code(body) == "rate_limit"

    def test_extract_from_string_error(self):
        adapter = FallbackAdapter()
        body = {"error": "Some error message"}
        assert adapter.extract_error_code(body) == "some error message"

    def test_extract_no_code(self):
        adapter = FallbackAdapter()
        assert adapter.extract_error_code({}) == ""
        assert adapter.extract_error_code({"error": {}}) == ""

    def test_extract_non_dict_body(self):
        adapter = FallbackAdapter()
        assert adapter.extract_error_code("string body") == ""
        assert adapter.extract_error_code(None) == ""


# ── Stream Options Tests ────────────────────────────────────────


class TestStreamOptions:
    def test_openai_uses_stream_options(self):
        adapter = OpenAIAdapter()
        assert adapter.custom_stream_options() == {"include_usage": True}

    def test_other_providers_no_stream_options(self):
        assert DeepSeekAdapter().custom_stream_options() is None
        assert MiniMaxAdapter().custom_stream_options() is None
        assert QwenAdapter().custom_stream_options() is None
        assert KimiAdapter().custom_stream_options() is None
        assert FallbackAdapter().custom_stream_options() is None


# ── Normalize Thinking Tests ──────────────────────────────────


class TestNormalizeThinking:
    def test_strips_code_blocks(self):
        adapter = FallbackAdapter()
        result = adapter.normalize_thinking("before\n```\nfoo\n```\nafter")
        assert "```" not in result
        # The content inside the code block is removed (stripped)
        assert "before" in result
        assert "after" in result

    def test_strips_inline_code(self):
        adapter = FallbackAdapter()
        result = adapter.normalize_thinking("use `variable` here")
        assert "`" not in result

    def test_strips_bold(self):
        adapter = FallbackAdapter()
        result = adapter.normalize_thinking("**bold** text")
        assert "**" not in result

    def test_strips_italic(self):
        adapter = FallbackAdapter()
        result = adapter.normalize_thinking("*italic* text")
        assert result == "italic text"

    def test_normalizes_whitespace(self):
        adapter = FallbackAdapter()
        result = adapter.normalize_thinking("multiple   spaces\n\t\there")
        assert "  " not in result
        assert "\n" not in result


# ── Quota Error Message Tests ──────────────────────────────────


class TestQuotaErrorMessage:
    def test_default_message(self):
        adapter = FallbackAdapter()
        assert "quota" in adapter.quota_error_message().lower()

    def test_minimax_specific_message(self):
        adapter = MiniMaxAdapter()
        msg = adapter.quota_error_message()
        assert "minimax" in msg.lower() or "MiniMax" in msg
        assert "5" in msg or "five" in msg.lower()


# ── Custom Headers Tests ───────────────────────────────────────


class TestCustomHeaders:
    def test_default_no_headers(self):
        adapter = OpenAIAdapter()
        assert adapter.custom_headers({}) == {}


# ── Custom Payload Tests ───────────────────────────────────────


class TestCustomPayload:
    def test_default_passthrough(self):
        adapter = OpenAIAdapter()
        payload = {"model": "x", "messages": []}
        assert adapter.custom_payload(payload, {}) == payload


# ── Reasoning Tag Patterns Tests ────────────────────────────────


class TestReasoningTagPatterns:
    def test_default_empty(self):
        adapter = OpenAIAdapter()
        assert adapter.reasoning_tag_patterns() == []


# ── Stream Chunk Integration Tests ─────────────────────────────


class TestStreamChunkWithProvider:
    """Test that parser integrates with provider adapter."""

    def test_chunk_with_minimax_think_tags(self):
        from strategy_research.core.llm.parser import _chunk_from_dict

        payload = {
            "choices": [{
                "delta": {"content": "<think>my plan</think>response text"}
            }]
        }
        chunk = _chunk_from_dict(payload, provider_name="minimax")
        assert chunk is not None
        assert chunk.delta_content == "response text"
        assert chunk.delta_thinking == "my plan"

    def test_chunk_with_deepseek_reasoning_content(self):
        from strategy_research.core.llm.parser import _chunk_from_dict

        payload = {
            "choices": [{
                "delta": {
                    "reasoning_content": "thinking",
                    "content": "response"
                }
            }]
        }
        chunk = _chunk_from_dict(payload, provider_name="deepseek")
        assert chunk is not None
        assert chunk.delta_content == "response"
        assert chunk.delta_thinking == "thinking"

    def test_chunk_without_provider(self):
        from strategy_research.core.llm.parser import _chunk_from_dict

        payload = {
            "choices": [{
                "delta": {"content": "<think>plan</think>response"}
            }]
        }
        # No provider → fallback → no thinking extraction from tags
        chunk = _chunk_from_dict(payload)
        assert chunk is not None
        # Content includes raw tags (fallback doesn't parse)
        assert "<think>" in chunk.delta_content
        assert chunk.delta_thinking == ""


# ── Chat Response Integration Tests ────────────────────────────


class TestChatResponseWithProvider:
    """Test that parse_chat_response integrates with provider adapter."""

    def test_response_with_minimax_thinking(self):
        from strategy_research.core.llm.parser import parse_chat_response

        raw = {
            "choices": [{
                "message": {
                    "content": "<think>my plan</think>response",
                    "tool_calls": []
                },
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        response = parse_chat_response(raw, provider_name="minimax")
        assert response.content == "response"
        assert response.reasoning_content == "my plan"

    def test_response_with_deepseek_reasoning(self):
        from strategy_research.core.llm.parser import parse_chat_response

        raw = {
            "choices": [{
                "message": {
                    "content": "response",
                    "reasoning_content": "thinking",
                    "tool_calls": []
                },
                "finish_reason": "stop"
            }]
        }
        response = parse_chat_response(raw, provider_name="deepseek")
        assert response.content == "response"
        assert response.reasoning_content == "thinking"