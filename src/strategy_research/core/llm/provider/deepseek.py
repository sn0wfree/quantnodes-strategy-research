"""DeepSeek provider adapter.

Uses OpenAI-compatible API. Thinking tokens come via `reasoning_content`.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class DeepSeekAdapter(ProviderAdapter):
    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def default_base_url(self) -> str:
        return "https://api.deepseek.com/v1"

    @property
    def default_model(self) -> str:
        return "deepseek-chat"

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = delta.get("reasoning_content")
        if content:
            return self.normalize_thinking(content)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = message.get("reasoning_content")
        if content:
            return self.normalize_thinking(content)
        return None