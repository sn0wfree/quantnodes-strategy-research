"""SiliconFlow (硅基流动) provider adapter.

https://platform.siliconflow.cn — OpenAI Chat Completions-compatible
endpoint. Most reasoning models served here (DeepSeek-R1 / V4-Flash,
QwQ-32B, GLM-Z1) emit thinking via ``reasoning_content``, so this
adapter inherits OpenAIReasoningFieldAdapter. See
docs/llm-provider-setup.md for onboarding params.
"""

from __future__ import annotations

from ._reasoning_field import OpenAIReasoningFieldAdapter


class SiliconFlowAdapter(OpenAIReasoningFieldAdapter):
    @property
    def name(self) -> str:
        return "siliconflow"

    @property
    def default_base_url(self) -> str:
        return "https://api.siliconflow.cn/v1"

    @property
    def default_model(self) -> str:
        return "deepseek-ai/DeepSeek-V4-Flash"

    @property
    def default_max_tokens(self) -> int:
        return 16384
