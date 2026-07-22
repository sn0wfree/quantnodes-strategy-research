"""Parser for OpenAI-compatible Chat Completions responses.

Handles:
    - Standard {role, content} messages
    - Tool calls (parsed with 3-stage JSON fallback)
    - Streaming chunks (SSE deltas)
    - Usage accounting
    - Finish reasons (stop / tool_calls / length / content_filter)

Provider-specific quirks are NOT handled here — callers should normalize
upstream responses to the OpenAI Chat Completions shape before parsing.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import LLMMalformedResponseError

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """A single tool invocation request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass
class LLMResponse:
    """Parsed non-streaming chat completion response."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"               # stop | tool_calls | length | content_filter
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)  # full original response

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
        }


@dataclass
class StreamChunk:
    """One chunk from a streaming response.

    In OpenAI's SSE protocol, content arrives as delta strings and tool_calls
    arrive incrementally (arguments may span multiple chunks).
    """

    delta_content: str = ""
    delta_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None  # only in final chunk (stream_options)


# ── Response parsing ────────────────────────────────────────────────


def parse_chat_response(raw: dict[str, Any]) -> LLMResponse:
    """Parse a complete Chat Completions response.

    Args:
        raw: The parsed JSON response dict.

    Returns:
        LLMResponse with content, tool_calls, finish_reason, usage.

    Raises:
        LLMMalformedResponseError: If structure is unexpected.
    """
    if not isinstance(raw, dict):
        raise LLMMalformedResponseError(
            f"response is not a dict: {type(raw).__name__}"
        )

    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise LLMMalformedResponseError(
            f"missing 'choices' array (got {type(choices).__name__})"
        )

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMMalformedResponseError("choice[0] is not a dict")

    message = first.get("message", {})
    if not isinstance(message, dict):
        raise LLMMalformedResponseError("choice[0].message is not a dict")

    content = message.get("content") or ""
    finish_reason = first.get("finish_reason") or "stop"

    # Parse tool_calls
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id", "")
            function = tc.get("function") or {}
            if not isinstance(function, dict):
                continue
            tc_name = function.get("name", "")
            raw_args = function.get("arguments", "")
            arguments = parse_tool_arguments(raw_args)
            tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=arguments))
    elif raw_tool_calls:  # non-empty but wrong type
        logger.warning("tool_calls field is not a list: %s", type(raw_tool_calls).__name__)

    # Parse usage
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    usage_clean: dict[str, int] = {}
    for k, v in usage.items():
        if isinstance(v, (int, float)):
            usage_clean[k] = int(v)

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=str(finish_reason),
        usage=usage_clean,
        raw=raw,
    )


# ── Tool argument parsing (3-stage fallback) ────────────────────────


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def parse_tool_arguments(raw_args: str | Any) -> dict[str, Any]:
    """Parse tool call arguments with 3-stage fallback.

    LLMs sometimes emit:
        1. Valid JSON: '{"a": 1}'                  → standard parse
        2. Markdown-fenced JSON: '```json\n{...}\n```' → extract + parse
        3. Sloppy JSON with extra text: 'Output: {...}' → strip non-JSON chars + parse

    Args:
        raw_args: String from tool_call.function.arguments (or already-parsed dict).

    Returns:
        Parsed dict. Returns {} if all stages fail (logged).
    """
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if not isinstance(raw_args, str):
        return {}

    s = raw_args.strip()
    if not s:
        return {}

    # Stage 1: standard JSON
    try:
        result = json.loads(s)
        if isinstance(result, dict):
            return result
        # Wrap non-dict in {"value": ...}
        return {"value": result}
    except json.JSONDecodeError:
        pass

    # Stage 2: extract markdown ```json ... ``` block
    m = _JSON_FENCE_RE.search(s)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict):
                return result
            return {"value": result}
        except json.JSONDecodeError:
            pass

    # Stage 3: strip leading non-JSON chars and try once more
    # Find first '{' and last '}'
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        candidate = s[start : end + 1]
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
            return {"value": result}
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse tool arguments: %r (truncated)", s[:80])
    return {}


# ── SSE stream parsing ──────────────────────────────────────────────


def parse_stream_chunk(raw_line: str) -> StreamChunk | None:
    """Parse one SSE line into a StreamChunk.

    Format (OpenAI):
        data: {json}
        data: [DONE]

    Returns None for empty lines or the [DONE] sentinel.
    """
    line = raw_line.strip()
    if not line:
        return None
    if line == "data: [DONE]":
        return StreamChunk(finish_reason="stop")
    if not line.startswith("data: "):
        return None

    payload_str = line[len("data: "):].strip()
    if not payload_str:
        return None
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        logger.warning("malformed SSE payload: %r", payload_str[:80])
        return None

    return _chunk_from_dict(payload)


def _chunk_from_dict(payload: dict[str, Any]) -> StreamChunk | None:
    """Convert a chunk payload dict to StreamChunk."""
    if not isinstance(payload, dict):
        return None

    choices = payload.get("choices")
    usage = payload.get("usage")
    usage_clean: dict[str, int] | None = None
    if isinstance(usage, dict):
        usage_clean = {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}

    if not isinstance(choices, list) or len(choices) == 0:
        # Some providers send usage-only chunks at end
        return StreamChunk(usage=usage_clean)

    first = choices[0]
    if not isinstance(first, dict):
        return None

    delta = first.get("delta") or {}
    finish_reason = first.get("finish_reason")

    delta_content = delta.get("content") or ""

    delta_tool_calls: list[dict[str, Any]] = []
    raw_dtc = delta.get("tool_calls")
    if isinstance(raw_dtc, list):
        for dtc in raw_dtc:
            if isinstance(dtc, dict):
                delta_tool_calls.append(dtc)

    return StreamChunk(
        delta_content=delta_content,
        delta_tool_calls=delta_tool_calls,
        finish_reason=str(finish_reason) if finish_reason else None,
        usage=usage_clean,
    )