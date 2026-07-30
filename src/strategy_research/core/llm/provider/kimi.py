"""Kimi (Moonshot) provider adapter.

Uses OpenAI-compatible API. No native thinking token support.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class KimiAdapter(ProviderAdapter):
    @property
    def name(self) -> str:
        return "kimi"

    @property
    def default_base_url(self) -> str:
        return "https://api.moonshot.cn/v1"

    @property
    def default_model(self) -> str:
        return "moonshot-v1-8k"

    @property
    def default_context_tokens(self) -> int:
        # moonshot-v1-8k is 8K; kimi-k2 is 128K. Default to 128K
        # since users typically use kimi-k2.5 (bundled default).
        return 128000

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        return None
