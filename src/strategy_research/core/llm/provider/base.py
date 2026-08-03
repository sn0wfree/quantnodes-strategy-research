"""Base interface for provider adapters.

Each LLM provider (OpenAI, DeepSeek, MiniMax, Qwen, Kimi, ...) gets its own
adapter that encapsulates ALL provider-specific behavior:
- Default endpoint, model, max_tokens
- Thinking/reasoning token extraction
- HTTP headers, payload modifications
- Error code parsing and exception mapping
- User-friendly error messages

Adding a new provider = create a new file in this directory + register it
in __init__.py. NO core file (parser.py, openai_client.py, loop.py) needs
to be modified.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any


class ProviderAdapter(ABC):
    """Provider-specific behavior adapter."""

    # ── Metadata (replaces PROVIDER_DEFAULTS) ────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'openai', 'deepseek', 'minimax')."""
        ...

    @property
    @abstractmethod
    def default_base_url(self) -> str:
        """Default API endpoint."""
        ...

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Default model name."""
        ...

    @property
    def default_max_tokens(self) -> int:
        """Default max output tokens."""
        return 8192

    @property
    def default_context_tokens(self) -> int:
        """Default context window size (input + output).

        Used as the static fallback when models.dev is unreachable.
        Override per-provider for a more accurate value.
        """
        return 8192

    # ── Thinking Tokens ──────────────────────────────────────────

    @abstractmethod
    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        """Extract thinking tokens from a streaming delta.

        Returns normalized thinking text, or None if no thinking present.
        """
        ...

    @abstractmethod
    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        """Extract thinking tokens from a non-streaming message.

        Returns normalized thinking text, or None if no thinking present.
        """
        ...

    def strip_thinking_from_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of delta with thinking tags/content removed.

        Override for providers (e.g. MiniMax) that embed thinking tokens
        inside content. Default: passthrough (thinking is in a separate field).
        """
        return dict(delta)

    def strip_thinking_from_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of message with thinking tags/content removed.

        Override for providers (e.g. MiniMax) that embed thinking tokens
        inside content. Default: passthrough.
        """
        return dict(message)

    def strip_dsml_from_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """Strip leaked DSML pseudo-tool-call markup from a streaming delta.

        DeepSeek-V4-Flash (and similar reasoning models) sometimes emit
        ``<tools><invoke name="X">...</invoke></tools>`` or
        ``[DSML | tool_calls>...<]`` blocks inside ``reasoning_content``
        / ``content`` to *express intent* to call a tool. The real tool
        call travels through ``delta.tool_calls`` in a separate stream,
        so these markup blocks in the text are pure noise for the user.

        Default: passthrough. Override in providers that serve DeepSeek
        reasoning models (see :class:`SiliconFlowAdapter`,
        :class:`DeepSeekAdapter`).
        """
        return dict(delta)

    def strip_dsml_from_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Message-level variant. Default: passthrough."""
        return dict(message)

    def normalize_thinking(self, text: str) -> str:
        """Normalize thinking content to plain text.

        Strips markdown code blocks, inline code, bold/italic, normalizes
        whitespace. Override for provider-specific normalization needs.
        """
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`[^`]+`", "", text)
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ── HTTP Layer ────────────────────────────────────────────────

    def custom_headers(self, config: Any) -> dict[str, str]:
        """Additional HTTP headers. Default: none beyond Bearer auth."""
        return {}

    def custom_payload(self, payload: dict[str, Any], config: Any) -> dict[str, Any]:
        """Modify request payload before sending. Default: passthrough."""
        return payload

    def custom_stream_options(self) -> dict[str, Any] | None:
        """Provider-specific stream_options field. None = don't send.

        OpenAI uses {"include_usage": True}. Other providers may differ.
        """
        return None

    # ── Error Handling ────────────────────────────────────────────

    def extract_error_code(self, body: Any) -> str:
        """Extract error code string from provider response body.

        Handles multiple JSON shapes:
            {"error": {"code": "..."}}
            {"error": "msg", "code": "..."}
            {"code": "..."}
        Returns empty string if no code found.
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

    def handle_error(self, status: int, body: Any) -> Exception | None:
        """Map HTTP status + body to a provider-specific exception.

        Return None to use the default status-code mapping.
        Override for provider-specific error semantics (e.g. MiniMax 403).
        """
        return None

    def quota_error_message(self) -> str:
        """User-friendly quota error message."""
        return "quota exceeded"

    # ── UI / TUI ──────────────────────────────────────────────────

    def reasoning_tag_patterns(self) -> list[str]:
        """Regex patterns for reasoning tags used in TUI display.

        Used by text_filters.py to strip reasoning tags from displayed text.
        """
        return []
