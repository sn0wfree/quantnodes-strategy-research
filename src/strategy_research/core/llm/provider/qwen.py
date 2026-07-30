"""Qwen (Aliyun DashScope) provider adapter.

Uses OpenAI-compatible mode. Thinking tokens come via `reasoning_content`
when enable_thinking is set on the model.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class QwenAdapter(ProviderAdapter):
    @property
    def name(self) -> str:
        return "qwen"

    @property
    def default_base_url(self) -> str:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def default_model(self) -> str:
        return "qwen-plus"

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
