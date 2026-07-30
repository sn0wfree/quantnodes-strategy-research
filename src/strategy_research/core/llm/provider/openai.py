"""OpenAI provider adapter.

Standard OpenAI Chat Completions API. Supports reasoning tokens via
the `reasoning` field for o1/o3 models.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    @property
    def name(self) -> str:
        return "openai"

    @property
    def default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    @property
    def default_model(self) -> str:
        return "gpt-4o-mini"

    @property
    def default_max_tokens(self) -> int:
        return 16384

    @property
    def default_context_tokens(self) -> int:
        # gpt-4o-mini is 128K. Older models (gpt-3.5-turbo) are 16K; use 128K.
        return 128000

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = delta.get("reasoning")
        if content and isinstance(content, str):
            return self.normalize_thinking(content)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = message.get("reasoning")
        if content and isinstance(content, str):
            return self.normalize_thinking(content)
        return None

    def custom_stream_options(self) -> dict[str, Any] | None:
        return {"include_usage": True}
