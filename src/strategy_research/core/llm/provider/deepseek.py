"""DeepSeek provider adapter.

Uses OpenAI-compatible API. Thinking tokens come via `reasoning_content`,
inherited from OpenAIReasoningFieldAdapter.
"""

from __future__ import annotations

from ._reasoning_field import OpenAIReasoningFieldAdapter


class DeepSeekAdapter(OpenAIReasoningFieldAdapter):
    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def default_base_url(self) -> str:
        return "https://api.deepseek.com/v1"

    @property
    def default_model(self) -> str:
        return "deepseek-chat"
