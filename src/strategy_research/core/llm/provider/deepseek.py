"""DeepSeek provider adapter.

Uses OpenAI-compatible API. Thinking tokens come via ``reasoning_content``,
inherited from :class:`OpenAIReasoningFieldAdapter`.

Also overrides ``strip_dsml_from_delta`` / ``strip_dsml_from_message``
to remove the ``<tools>...</tools>`` / ``[DSML | ...<]`` pseudo-tool-call
leakage that DeepSeek reasoning models emit inside
``reasoning_content`` / ``content``. Real tool calls still travel
through the structured ``delta.tool_calls`` path.
"""

from __future__ import annotations

from ._dsml_patterns import strip_dsml_text
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

    # ── DSML filter (DeepSeek reasoning_content leakage) ──

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
