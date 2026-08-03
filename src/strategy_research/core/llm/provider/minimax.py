"""MiniMax provider adapter.

Special handling:
- Thinking tokens emitted as <think> tags in delta.content
- Uses 403 to signal quota exhaustion (5-hour rolling limit)
- Uses 429 to signal either quota or per-minute rate limit
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import LLMConfigError, LLMQuotaError
from .base import ProviderAdapter


class MiniMaxAdapter(ProviderAdapter):
    THINK_PATTERN = re.compile(r"<think>([\s\S]*?)<\/think>")
    THINK_OPEN = "<think>"
    THINK_CLOSE = "</think>"

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def default_base_url(self) -> str:
        return "https://api.minimaxi.com/v1"

    @property
    def default_model(self) -> str:
        return "minimax-M3"

    @property
    def default_max_tokens(self) -> int:
        return 32000

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = delta.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            # BPE chunk boundary: keep leading spaces inside the tags.
            return self.normalize_thinking(match.group(1), strip_edges=False)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = message.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            return self.normalize_thinking(match.group(1))
        return None

    def sanitize_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        content = delta.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content)
            out = dict(delta)
            out["content"] = cleaned
            return out
        return dict(delta)

    def sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content).strip()
            out = dict(message)
            out["content"] = cleaned
            return out
        return dict(message)

    def handle_error(self, status: int, body: Any) -> Exception | None:
        # MiniMax uses 403 for quota exhaustion, and 429 can be either
        # quota or per-minute rate limit.
        if status in (401, 403):
            error_code = self.extract_error_code(body)
            if "quota" in error_code or "billing" in error_code:
                return LLMQuotaError(f"quota exceeded ({status}): {body}")
        if status == 429:
            error_code = self.extract_error_code(body)
            if "quota" in error_code or "billing" in error_code:
                return LLMQuotaError(f"quota exceeded (429): {body}")
        # MiniMax-specific: 400 with code 2013 means "chat content is empty"
        # This usually happens when over-compression leaves an empty context.
        # Map to LLMConfigError so:
        # 1. _is_stream_required_error returns True (no stream→achat fallback)
        #    — retrying via non-streaming won't help.
        # 2. The error is propagated as a user-visible configuration error
        #    with a clear "send new message or create new session" message.
        if status == 400:
            body_str = str(body)
            error_code = self.extract_error_code(body)
            if "2013" in body_str or "chat content is empty" in body_str.lower() \
                    or "2013" in error_code:
                return LLMConfigError(
                    f"empty chat content (MiniMax 2013): {body}. "
                    f"This usually means the conversation history was over-compressed. "
                    f"Please send a new message or create a new session."
                )
        return None

    def quota_error_message(self) -> str:
        return "MiniMax 配额已用完（5小时限额）"
