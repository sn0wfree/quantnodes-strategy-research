"""LLM client and configuration for strategy-research.

Public API:
    LLMConfig           - immutable config dataclass with 4-layer merge
    OpenAICompatClient  - OpenAI/DeepSeek/Kimi/Qwen unified client (PR5-c2)

Layered configuration (higher overrides lower):
    1. Code defaults        (LLMConfig field defaults)
    2. YAML profile file    (~/.quantnodes-research/llm.yaml)
    3. Environment variables (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
                             STRATEGY_RESEARCH_LLM_PROFILE)
    4. CLI overrides        (parsed argparse namespace)

Typical usage:

    from strategy_research.core.llm import LLMConfig

    cfg = LLMConfig.load()
    # override temperature at runtime
    cfg2 = cfg.with_config(temperature=0.3)
"""

from __future__ import annotations

from .config import LLMConfig

__all__ = ["LLMConfig"]