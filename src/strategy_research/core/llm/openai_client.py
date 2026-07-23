"""OpenAI-compatible chat completion client.

Supports any provider that follows the OpenAI Chat Completions API:
    - OpenAI      (default)
    - DeepSeek    (api.deepseek.com/v1)
    - Kimi        (api.moonshot.cn/v1)
    - Qwen        (dashscope.aliyuncs.com/compatible-mode/v1)
    - Custom      (any base_url + model)

Features:
    - Sync (chat) + Async (achat) + Streaming (stream)
    - Retry with exponential backoff on 429/5xx
    - 4-stage error mapping (401/403/429/5xx/timeout)
    - Proxy support
    - Tool calls with auto JSON parsing (delegated to parser)
    - with_config(**kw) returns a derived client
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Iterator

import httpx

from .config import LLMConfig
from .errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMMalformedResponseError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from .parser import LLMResponse, StreamChunk, parse_chat_response, parse_stream_chunk

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _backoff_delay(attempt: int, base: float) -> float:
    """Exponential backoff: base * 2^attempt, capped at 60s."""
    return min(base * (2 ** attempt), 60.0)


def _ensure_api_key(config: LLMConfig) -> str:
    if not config.api_key:
        raise LLMConfigError(
            "API key not configured. Set OPENAI_API_KEY environment variable "
            "or pass api_key explicitly to LLMConfig."
        )
    return config.api_key


def _build_headers(config: LLMConfig) -> dict[str, str]:
    api_key = _ensure_api_key(config)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_payload(
    config: LLMConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Build OpenAI Chat Completions request body."""
    payload: dict[str, Any] = {
        "model": overrides.get("model") or config.model,
        "messages": list(messages),
    }

    # Sampling params (use overrides if provided, else config)
    if "temperature" in overrides or config.temperature is not None:
        payload["temperature"] = overrides.get("temperature", config.temperature)
    if "top_p" in overrides or config.top_p != 1.0:
        payload["top_p"] = overrides.get("top_p", config.top_p)
    payload["max_tokens"] = overrides.get("max_tokens", config.max_tokens)
    if config.frequency_penalty:
        payload["frequency_penalty"] = config.frequency_penalty
    if config.presence_penalty:
        payload["presence_penalty"] = config.presence_penalty
    if config.stop:
        payload["stop"] = list(config.stop)
    if config.seed is not None:
        payload["seed"] = config.seed

    # Tools
    if tools:
        payload["tools"] = list(tools)
        payload["tool_choice"] = tool_choice or config.tool_choice
        payload["parallel_tool_calls"] = config.parallel_tool_calls

    return payload


def _raise_for_status(response: httpx.Response) -> None:
    """Map httpx status to LLM-specific exception."""
    status = response.status_code
    if status < 400:
        return
    try:
        body = response.json()
    except Exception:                              # noqa: BLE001
        body = {"raw": response.text[:500]}

    if status in (401, 403):
        raise LLMAuthError(f"auth failed ({status}): {body}")
    if status == 429:
        raise LLMRateLimitError(f"rate limited (429): {body}")
    if 500 <= status < 600:
        raise LLMServerError(f"server error ({status}): {body}")
    raise LLMError(f"unexpected status {status}: {body}")


# ── Client ───────────────────────────────────────────────────────────


class OpenAICompatClient:
    """OpenAI-compatible chat completion client.

    Example:
        config = LLMConfig.load(profile="deepseek")
        client = OpenAICompatClient(config)
        resp = client.chat([{"role": "user", "content": "hi"}])
        print(resp.content)

        # Tool calls
        resp = client.chat(
            messages, tools=[{"type": "function",
                              "function": {"name": "read_file", ...}}]
        )
        for tc in resp.tool_calls:
            ...

        # Override at call time
        resp = client.chat(messages, temperature=0.2, model="gpt-4o")

        # Streaming
        for chunk in client.stream(messages):
            print(chunk.delta_content, end="")
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    # ── Public API ─────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> LLMResponse:
        """Synchronous chat completion with retry."""
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = False

        response = self._request_with_retry(payload, stream=False)
        try:
            raw = response.json()
        except json.JSONDecodeError as exc:
            raise LLMMalformedResponseError(
                f"response is not JSON: {response.text[:200]}"
            ) from exc
        return parse_chat_response(raw)

    async def achat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> LLMResponse:
        """Async chat completion with retry."""
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = False

        response = await self._arequest_with_retry(payload)
        try:
            raw = response.json()
        except json.JSONDecodeError as exc:
            raise LLMMalformedResponseError(
                f"response is not JSON: {response.text[:200]}"
            ) from exc
        return parse_chat_response(raw)

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> Iterator[StreamChunk]:
        """SSE streaming chat completion.

        Yields StreamChunk objects. The last chunk has finish_reason set.
        Does NOT retry mid-stream (network errors → LLMError).
        """
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = True
        # OpenAI uses stream_options to include usage in final chunk
        payload["stream_options"] = {"include_usage": True}

        headers = _build_headers(self.config)
        url = self._chat_url()

        client_kwargs: dict[str, Any] = {"timeout": self.config.timeout_s}
        if self.config.proxy:
            client_kwargs["proxy"] = self.config.proxy

        try:
            with httpx.Client(**client_kwargs) as client:
                with client.stream("POST", url, json=payload, headers=headers) as response:
                    _raise_for_status(response)
                    for line in response.iter_lines():
                        chunk = parse_stream_chunk(line)
                        if chunk is not None:
                            yield chunk
                            if chunk.finish_reason:
                                return
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"stream timed out after {self.config.timeout_s}s"
            ) from exc

    async def astream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Async SSE streaming chat completion."""
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        headers = _build_headers(self.config)
        url = self._chat_url()

        client_kwargs: dict[str, Any] = {"timeout": self.config.timeout_s}
        if self.config.proxy:
            client_kwargs["proxy"] = self.config.proxy

        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                _raise_for_status(response)
                async for line in response.aiter_lines():
                    chunk = parse_stream_chunk(line)
                    if chunk is not None:
                        yield chunk
                        if chunk.finish_reason:
                            return

    def with_config(self, **kwargs: Any) -> "OpenAICompatClient":
        """Return a new client with overridden config fields."""
        return OpenAICompatClient(self.config.with_config(**kwargs))

    # ── Internal helpers ──────────────────────────

    def _chat_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def _request_with_retry(
        self, payload: dict[str, Any], *, stream: bool
    ) -> httpx.Response:
        """Sync HTTP request with retry on transient failures."""
        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs: dict[str, Any] = {"timeout": self.config.timeout_s}
        if self.config.proxy:
            client_kwargs["proxy"] = self.config.proxy

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self.config.max_retries:
            try:
                with httpx.Client(**client_kwargs) as client:
                    response = client.post(url, json=payload, headers=headers)
                if response.status_code < 400:
                    return response
                # Determine if retryable
                if not _is_retryable_status(response.status_code):
                    _raise_for_status(response)  # raises
                # retryable
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                logger.warning(
                    "retryable status %s (attempt %d/%d); sleeping %.1fs",
                    response.status_code, attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)
                attempt += 1
                last_exc = LLMServerError(f"status {response.status_code}")
            except httpx.TimeoutException as exc:
                if attempt >= self.config.max_retries:
                    raise LLMTimeoutError(
                        f"request timed out after {self.config.timeout_s}s "
                        f"({attempt + 1} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                logger.warning(
                    "timeout (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)
                attempt += 1
                last_exc = exc

        if last_exc is not None:
            raise last_exc
        # Should not reach here, but safety net
        raise LLMError("max retries exhausted")

    async def _arequest_with_retry(
        self, payload: dict[str, Any]
    ) -> httpx.Response:
        """Async HTTP request with retry on transient failures."""
        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs: dict[str, Any] = {"timeout": self.config.timeout_s}
        if self.config.proxy:
            client_kwargs["proxy"] = self.config.proxy

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self.config.max_retries:
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(url, json=payload, headers=headers)
                if response.status_code < 400:
                    return response
                if not _is_retryable_status(response.status_code):
                    _raise_for_status(response)
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                logger.warning(
                    "async retryable status %s (attempt %d/%d); sleeping %.1fs",
                    response.status_code, attempt + 1, self.config.max_retries, delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
                last_exc = LLMServerError(f"status {response.status_code}")
            except httpx.TimeoutException as exc:
                if attempt >= self.config.max_retries:
                    raise LLMTimeoutError(
                        f"request timed out after {self.config.timeout_s}s "
                        f"({attempt + 1} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                await asyncio.sleep(delay)
                attempt += 1
                last_exc = exc

        if last_exc is not None:
            raise last_exc
        raise LLMError("max retries exhausted")
