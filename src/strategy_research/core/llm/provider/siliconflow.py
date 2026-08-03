"""SiliconFlow (硅基流动) provider adapter.

https://platform.siliconflow.cn — OpenAI Chat Completions-compatible
endpoint. Most reasoning models served here (DeepSeek-R1 / V4-Flash,
QwQ-32B, GLM-Z1) emit thinking via ``reasoning_content``, so this
adapter inherits OpenAIReasoningFieldAdapter. See
docs/llm-provider-setup.md for onboarding params.

Also overrides ``sanitize_delta`` / ``sanitize_message``
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

    def extract_thinking_from_delta(self, delta):
        content = delta.get("reasoning_content")
        if content:
            # Strip DSML leakage first — extract must read clean text.
            return self.normalize_thinking(
                strip_dsml_text(content, strip_edges=False),
                strip_edges=False,
            )
        return None

    def extract_thinking_from_message(self, message):
        content = message.get("reasoning_content")
        if content:
            return self.normalize_thinking(strip_dsml_text(content))
        return None

    def sanitize_delta(self, delta):
        out = dict(delta)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = strip_dsml_text(v, strip_edges=False)
        return out

    def sanitize_message(self, message):
        out = dict(message)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = strip_dsml_text(v)
        return out
