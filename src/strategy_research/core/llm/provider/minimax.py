"""MiniMax provider adapter.

Special handling:
- Thinking tokens emitted as <think> tags in delta.content
- Uses 403 to signal quota exhaustion (5-hour rolling limit)
- Uses 429 to signal either quota or per-minute rate limit
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import LLMQuotaError
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

    @property
    def default_context_tokens(self) -> int:
        # Conservative fallback: minimax-M3 (minimax-cn-coding-plan) is 1M,
        # but older generations are 200K. Use 200K so callers see "we're
        # approaching the limit" before the actual limit.
        return 200000

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = delta.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            return self.normalize_thinking(match.group(1))
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = message.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            return self.normalize_thinking(match.group(1))
        return None

    def strip_thinking_from_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        content = delta.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content).strip()
            out = dict(delta)
            out["content"] = cleaned
            return out
        return delta

    def strip_thinking_from_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content).strip()
            out = dict(message)
            out["content"] = cleaned
            return out
        return message

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
        return None

    def quota_error_message(self) -> str:
        return "MiniMax 配额已用完（5小时限额）"
