"""SiliconFlow (硅基流动) provider adapter.

https://platform.siliconflow.cn — OpenAI Chat Completions-compatible
endpoint. Most reasoning models served here (DeepSeek-R1 / V4-Flash,
QwQ-32B, GLM-Z1) emit thinking via ``reasoning_content``, so this
adapter inherits OpenAIReasoningFieldAdapter. See
docs/llm-provider-setup.md for onboarding params.

Also overrides ``strip_dsml_from_delta`` / ``strip_dsml_from_message``
to remove the DeepSeek-V4-Flash ``<tools>...</tools>`` /
``[DSML | ...<]`` pseudo-tool-call leakage that the model emits inside
``reasoning_content`` / ``content``. Real tool calls still travel
through the structured ``delta.tool_calls`` path; the in-text blocks
are pure UI noise.
"""

from __future__ import annotations

from ._dsml_patterns import strip_dsml_text
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

    @property
    def default_context_tokens(self) -> int:
        # DeepSeek-V4-Flash serves a 128K context window (DeepSeek
        # lineage). Without this override the conservative 8192 base
        # default would trigger compaction far too early.
        return 131072

    # ── DSML filter (DeepSeek-V4-Flash reasoning_content leakage) ──

    def strip_dsml_from_delta(self, delta):
        out = dict(delta)
        for key in ("reasoning_content", "content"):
            v = out.get(key)
            if isinstance(v, str) and v:
                out[key] = strip_dsml_text(v)
        return out

    def strip_dsml_from_message(self, message):
        out = dict(message)
        for key in ("reasoning_content", "content"):
            v = out.get(key)
            if isinstance(v, str) and v:
                out[key] = strip_dsml_text(v)
        return out
