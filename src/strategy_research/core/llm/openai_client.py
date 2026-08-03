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
import datetime
import email.utils
import json
import logging
import random
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


def _parse_retry_after(header_value: str) -> float | None:
    """Parse Retry-After header (seconds or HTTP-date). Returns seconds or None."""
    if not header_value:
        return None
    # Try seconds (int)
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass
    # Try HTTP-date
    try:
        dt = email.utils.parsedate_to_datetime(header_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        delta = dt - datetime.datetime.now(datetime.timezone.utc)
        return max(0.0, delta.total_seconds())
    except (TypeError, ValueError):
        return None


def _compute_retry_delay(
    response: httpx.Response,
    attempt: int,
    base_backoff: float,
    max_backoff: float = 60.0,
    jitter_fraction: float = 0.3,
) -> float:
    """Compute retry delay with exponential backoff, Retry-After, and jitter.

    Args:
        response: the HTTP response with Retry-After header
        attempt: zero-based attempt index (0 = first retry)
        base_backoff: base backoff in seconds
        max_backoff: maximum backoff cap in seconds
        jitter_fraction: random jitter ±fraction (0.3 = ±30%)

    Returns:
        Delay in seconds.
    """
    # Start with exponential backoff
    delay = _backoff_delay(attempt, base_backoff)
    # Honor Retry-After if present
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        ra_seconds = _parse_retry_after(retry_after)
        if ra_seconds is not None:
            delay = max(delay, min(ra_seconds, max_backoff * 5))
    # Apply ±jitter_fraction random jitter
    if jitter_fraction > 0:
        jitter = random.uniform(1 - jitter_fraction, 1 + jitter_fraction)
        delay = delay * jitter
    # Final cap
    return min(delay, max_backoff)


def _ensure_api_key(config: LLMConfig) -> str:
    if not config.api_key:
        raise LLMConfigError(
            "API key not configured. Set OPENAI_API_KEY environment variable "
            "or pass api_key explicitly to LLMConfig."
        )
    return config.api_key


def _build_headers(config: LLMConfig) -> dict[str, str]:
    from .provider import get_provider

    api_key = _ensure_api_key(config)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    adapter = get_provider(config.provider)
    headers.update(adapter.custom_headers(config))
    return headers


def _build_payload(
    config: LLMConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Build OpenAI Chat Completions request body."""
    from .provider import get_provider

    payload: dict[str, Any] = {
        "model": overrides.get("model") or config.model,
        "messages": list(messages),
    }

    # Sampling params (use overrides if provided, else config)
    if "temperature" in overrides or config.temperature is not None:
        payload["temperature"] = overrides.get("temperature", config.temperature)
    if "top_p" in overrides or config.top_p != 1.0:
        payload["top_p"] = overrides.get("top_p", config.top_p)
    # ``max_tokens`` may be ``None`` if neither user nor provider set it.
    # Skip the field entirely in that case so the provider uses its own default.
    _max_tokens = overrides.get("max_tokens", config.max_tokens)
    if _max_tokens is not None:
        payload["max_tokens"] = _max_tokens
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

    # Provider-specific payload modifications
    adapter = get_provider(config.provider)
    payload = adapter.custom_payload(payload, config)

    # Provider-specific stream_options
    stream_opts = adapter.custom_stream_options()
    if stream_opts is not None:
        payload["stream_options"] = stream_opts

    return payload


def _extract_error_code(body: Any) -> str:
    """Extract error code from provider response body (lowercased).

    Handles common shapes:
        {"error": {"code": "quota_exceeded", ...}}
        {"error": "some message", "code": "quota_exceeded"}
        {"code": "quota_exceeded"}
    Returns empty string when no code is found.
    """
    if not isinstance(body, dict):
        return ""
    error_section = body.get("error", {})
    if isinstance(error_section, dict) and error_section.get("code"):
        return str(error_section["code"]).lower()
    if isinstance(error_section, str) and error_section:
        return error_section.lower()
    if body.get("code"):
        return str(body["code"]).lower()
    return ""


def _raise_for_status(response: httpx.Response, provider_name: str = "auto") -> None:
    """Map httpx status to LLM-specific exception.

    Provider-specific error semantics are delegated to the ProviderAdapter
    (e.g. MiniMax uses 403 for quota, not auth failure).
    """
    from .provider import get_provider

    status = response.status_code
    if status < 400:
        return
    try:
        body = response.json()
    except Exception:                              # noqa: BLE001
        body = {"raw": response.text[:500]}

    # Provider-specific error mapping (e.g. MiniMax 403-as-quota)
    adapter = get_provider(provider_name)
    custom_exception = adapter.handle_error(status, body)
    if custom_exception is not None:
        raise custom_exception

    # Default status-code mapping
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

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config
        self._transport = transport

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
        return parse_chat_response(raw, provider_name=self.config.provider)

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
        return parse_chat_response(raw, provider_name=self.config.provider)

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> Iterator[StreamChunk]:
        """SSE streaming chat completion with retry on transient failures.

        Yields StreamChunk objects. The last chunk has finish_reason set.
        Retries only BEFORE the first chunk is yielded (HTTP errors,
        connection failures, timeouts before stream starts).
        Does NOT retry mid-stream (after first chunk yielded → LLMError).
        """  # noqa: C901
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = True
        # stream_options moved to ProviderAdapter.custom_stream_options()

        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs = self._client_kwargs()

        last_response: httpx.Response | None = None
        for attempt in range(self.config.max_retries):
            started = False
            try:
                with httpx.Client(**client_kwargs) as client:
                    with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code >= 400:
                            response.read()
                        last_response = response
                        # Retryable HTTP error before any content
                        if _is_retryable_status(response.status_code):
                            if attempt == self.config.max_retries - 1:
                                _raise_for_status(response, self.config.provider)
                            delay = _compute_retry_delay(
                                response, attempt, self.config.retry_backoff_s
                            )
                            logger.warning(
                                "stream retryable status %s (attempt %d/%d); sleeping %.1fs",
                                response.status_code, attempt + 1, self.config.max_retries, delay,
                            )
                            time.sleep(delay)
                            continue
                        # Non-retryable error → raise immediately
                        if response.status_code >= 400:
                            _raise_for_status(response, self.config.provider)
                        # Stream content
                        try:
                            for line in response.iter_lines():
                                chunk = parse_stream_chunk(line, self.config.provider)
                                if chunk is not None:
                                    started = True
                                    yield chunk
                                    if chunk.finish_reason:
                                        return
                        finally:
                            try:
                                for _ in response.iter_lines():
                                    pass
                            except Exception:  # noqa: BLE001
                                pass
                # If we got here with no yield, it's an empty stream — exit
                return
            except httpx.TimeoutException as exc:
                if started or attempt == self.config.max_retries - 1:
                    raise LLMTimeoutError(
                        f"stream timed out after {self.config.timeout_s}s "
                        f"({self.config.max_retries} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "stream timeout (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)
            except httpx.TransportError as exc:
                if started or attempt == self.config.max_retries - 1:
                    raise LLMError(f"stream transport error: {exc}") from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "stream transport error (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)

        # Exhausted retries
        if last_response is not None:
            _raise_for_status(last_response, self.config.provider)
        raise LLMError("stream max retries exhausted")

    async def astream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **overrides: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Async SSE streaming chat completion with retry on transient failures.

        Retries only BEFORE the first chunk is yielded.
        Does NOT retry mid-stream (after first chunk yielded → LLMError).
        """  # noqa: C901
        payload = _build_payload(self.config, messages, tools, tool_choice, overrides)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs = self._client_kwargs()

        last_response: httpx.Response | None = None
        for attempt in range(self.config.max_retries):
            started = False
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code >= 400:
                            await response.aread()
                        last_response = response
                        # Retryable HTTP error before any content
                        if _is_retryable_status(response.status_code):
                            if attempt == self.config.max_retries - 1:
                                _raise_for_status(response, self.config.provider)
                            delay = _compute_retry_delay(
                                response, attempt, self.config.retry_backoff_s
                            )
                            logger.warning(
                                "astream retryable status %s (attempt %d/%d); sleeping %.1fs",
                                response.status_code, attempt + 1, self.config.max_retries, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        # Non-retryable error → raise immediately
                        if response.status_code >= 400:
                            _raise_for_status(response, self.config.provider)
                        # Stream content
                        try:
                            async for line in response.aiter_lines():
                                if not started and line.startswith("data: "):
                                    logger.debug("[DIAG] astream first raw line: %.200s", line)
                                chunk = parse_stream_chunk(line, self.config.provider)
                                if chunk is not None:
                                    started = True
                                    if not hasattr(chunk, '_diag_logged'):
                                        logger.debug(
                                            "[DIAG] astream chunk: delta_content=%.100r delta_thinking=%.100r "
                                            "finish_reason=%r tool_calls=%d usage=%r",
                                            chunk.delta_content[:100] if chunk.delta_content else "",
                                            chunk.delta_thinking[:100] if chunk.delta_thinking else "",
                                            chunk.finish_reason,
                                            len(chunk.delta_tool_calls),
                                            chunk.usage,
                                        )
                                        chunk._diag_logged = True  # type: ignore[attr-defined]
                                    yield chunk
                                    if chunk.finish_reason:
                                        return
                        finally:
                            try:
                                async for _ in response.aiter_lines():
                                    pass
                            except Exception:  # noqa: BLE001
                                pass
                # Empty stream
                return
            except httpx.TimeoutException as exc:
                if started or attempt == self.config.max_retries - 1:
                    raise LLMTimeoutError(
                        f"stream timed out after {self.config.timeout_s}s "
                        f"({self.config.max_retries} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "astream timeout (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                await asyncio.sleep(delay)
            except httpx.TransportError as exc:
                if started or attempt == self.config.max_retries - 1:
                    raise LLMError(f"stream transport error: {exc}") from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "astream transport error (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                await asyncio.sleep(delay)

        if last_response is not None:
            _raise_for_status(last_response, self.config.provider)
        raise LLMError("stream max retries exhausted")

    def with_config(self, **kwargs: Any) -> "OpenAICompatClient":
        """Return a new client with overridden config fields."""
        return OpenAICompatClient(self.config.with_config(**kwargs))

    # ── Internal helpers ──────────────────────────

    def _chat_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def _client_kwargs(self) -> dict[str, Any]:
        """Build httpx client kwargs (timeout, proxy, optional transport)."""
        kwargs: dict[str, Any] = {"timeout": self.config.timeout_s}
        if self.config.proxy:
            kwargs["proxy"] = self.config.proxy
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return kwargs

    def _request_with_retry(
        self, payload: dict[str, Any], *, stream: bool
    ) -> httpx.Response:
        """Sync HTTP request with retry on transient failures (total attempts = max_retries)."""
        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs = self._client_kwargs()

        last_response: httpx.Response | None = None
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(**client_kwargs) as client:
                    response = client.post(url, json=payload, headers=headers)
                if response.status_code < 400:
                    return response
                last_response = response
                # Non-retryable → raise immediately
                if not _is_retryable_status(response.status_code):
                    _raise_for_status(response, self.config.provider)  # raises
                # Retryable but last attempt → raise final error
                if attempt == self.config.max_retries - 1:
                    _raise_for_status(response, self.config.provider)
                # Retry
                delay = _compute_retry_delay(
                    response, attempt, self.config.retry_backoff_s
                )
                logger.warning(
                    "retryable status %s (attempt %d/%d); sleeping %.1fs",
                    response.status_code, attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)
            except httpx.TimeoutException as exc:
                if attempt == self.config.max_retries - 1:
                    raise LLMTimeoutError(
                        f"request timed out after {self.config.timeout_s}s "
                        f"({self.config.max_retries} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "timeout (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                time.sleep(delay)

        # Should not reach here
        if last_response is not None:
            _raise_for_status(last_response, self.config.provider)
        raise LLMError("max retries exhausted")

    async def _arequest_with_retry(
        self, payload: dict[str, Any]
    ) -> httpx.Response:
        """Async HTTP request with retry on transient failures (total attempts = max_retries)."""
        headers = _build_headers(self.config)
        url = self._chat_url()
        client_kwargs = self._client_kwargs()

        last_response: httpx.Response | None = None
        for attempt in range(self.config.max_retries):
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(url, json=payload, headers=headers)
                if response.status_code < 400:
                    return response
                last_response = response
                if not _is_retryable_status(response.status_code):
                    _raise_for_status(response, self.config.provider)
                if attempt == self.config.max_retries - 1:
                    _raise_for_status(response, self.config.provider)
                delay = _compute_retry_delay(
                    response, attempt, self.config.retry_backoff_s
                )
                logger.warning(
                    "async retryable status %s (attempt %d/%d); sleeping %.1fs",
                    response.status_code, attempt + 1, self.config.max_retries, delay,
                )
                await asyncio.sleep(delay)
            except httpx.TimeoutException as exc:
                if attempt == self.config.max_retries - 1:
                    raise LLMTimeoutError(
                        f"request timed out after {self.config.timeout_s}s "
                        f"({self.config.max_retries} attempts)"
                    ) from exc
                delay = _backoff_delay(attempt, self.config.retry_backoff_s)
                jitter = random.uniform(0.7, 1.3)
                delay = min(delay * jitter, 60.0)
                logger.warning(
                    "async timeout (attempt %d/%d); sleeping %.1fs",
                    attempt + 1, self.config.max_retries, delay,
                )
                await asyncio.sleep(delay)

        if last_response is not None:
            _raise_for_status(last_response, self.config.provider)
        raise LLMError("max retries exhausted")
