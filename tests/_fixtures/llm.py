"""LLM client fixtures — mock chat clients for unit tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


class MockLLMClient:
    """Minimal stand-in for OpenAICompatClient.

    Always returns the configured ``response_text`` regardless of input.
    Implements only ``chat`` / ``achat`` (whichever tests need) so it can
    stand in anywhere ``OpenAICompatClient`` is used.

    Example:
        >>> client = MockLLMClient('{"passed": true, "score": 0.9}')
        >>> resp = client.chat(messages=[{"role": "user", "content": "x"}])
        >>> resp.content
        '{"passed": true, "score": 0.9}'
    """

    def __init__(
        self,
        response_text: str = "",
        *,
        chat_error: Exception | None = None,
        response_factory: Any | None = None,
    ) -> None:
        """Construct a mock client.

        Args:
            response_text: Default response for ``chat()`` calls.
            chat_error: If set, ``chat()`` raises this exception.
            response_factory: Optional callable(messages, **kwargs) -> str.
                Lets tests vary response per call.
        """
        self.response_text = response_text
        self.chat_error = chat_error
        self.response_factory = response_factory
        self.call_count = 0
        self.last_messages: list[dict[str, Any]] = []
        self.last_kwargs: dict[str, Any] = {}

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> MagicMock:
        self.call_count += 1
        self.last_messages = messages
        self.last_kwargs = kwargs
        if self.chat_error:
            raise self.chat_error
        text = (
            self.response_factory(messages, **kwargs)
            if self.response_factory
            else self.response_text
        )
        resp = MagicMock()
        resp.content = text
        return resp

    async def achat(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> MagicMock:
        # Re-use chat() but as coroutine
        resp = self.chat(messages, **kwargs)
        return resp


def make_mock_chat_response(
    content: str,
    *,
    role: str = "assistant",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a raw chat-response dict (mimics the parser's input)."""
    msg: dict[str, Any] = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def make_mock_stream_chunks(
    contents: list[str],
    *,
    finish_reason: str = "stop",
    reasoning: list[str] | None = None,
) -> list[str]:
    """Build a list of raw SSE ``data: {...}`` strings for streaming tests."""
    import json
    out: list[str] = []
    reasoning = reasoning or [""] * len(contents)
    for i, (content, think) in enumerate(zip(contents, reasoning)):
        delta: dict[str, Any] = {}
        if content:
            delta["content"] = content
        if think:
            delta["reasoning_content"] = think
        is_last = i == len(contents) - 1
        chunk = {
            "choices": [{
                "delta": delta,
                "finish_reason": finish_reason if is_last else None,
            }],
        }
        out.append(f"data: {json.dumps(chunk)}")
    out.append("data: [DONE]")
    return out
