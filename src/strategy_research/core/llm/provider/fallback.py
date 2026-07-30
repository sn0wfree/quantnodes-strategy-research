"""Fallback adapter for unknown providers.

Returns safe defaults and ignores thinking tokens. Used when a provider
name is not registered in the adapter registry.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class FallbackAdapter(ProviderAdapter):
    @property
    def name(self) -> str:
        return "fallback"

    @property
    def default_base_url(self) -> str:
        return ""

    @property
    def default_model(self) -> str:
        return ""

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        return None