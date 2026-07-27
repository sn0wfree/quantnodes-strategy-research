"""Text filters for TUI display.

Centralised regex-based filters applied at the display boundary to
remove content that should never reach the user, even when the model
emits it as part of its streamed response.
"""
from __future__ import annotations

import re


# ── thinking / reasoning tag stripping ──────────────────────────────

# Closed-form tags emitted by various providers when surfacing their
# internal reasoning alongside the user-visible answer:
#   - <think>...</think>      (OpenAI o1-style / Anthropic extended thinking)
#   - <reasoning>...</reasoning>
#   - <thinking>...</thinking>
#   - <|reasoning|>...</|reasoning|>  (Qwen / DeepSeek)
#   - <|thinking|>...</|thinking|>
#   - <think/>                (self-closing marker)
_THINK_PATTERNS_CLOSED = [
    re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL),
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r"<\|reasoning\|>.*?<\|/reasoning\|>", re.DOTALL),
    re.compile(r"<\|thinking\|>.*?<\|/thinking\|>", re.DOTALL),
    re.compile(r"<think\s*/>", re.DOTALL),
]

# Unclosed (truncated mid-stream) forms. Strip from the opening tag
# to end of string — the rest is presumed to be reasoning content.
_THINK_PATTERNS_UNCLOSED = [
    re.compile(r"<think(?:ing)?>.*$", re.DOTALL),
    re.compile(r"<reasoning>.*$", re.DOTALL),
    re.compile(r"<thinking>.*$", re.DOTALL),
    re.compile(r"<\|reasoning\|>.*$", re.DOTALL),
    re.compile(r"<\|thinking\|>.*$", re.DOTALL),
]


def strip_thinking_tags(text: str) -> str:
    """Remove all reasoning / thinking tags from LLM output.

    Closed tags (``<think>...</think>``) are removed entirely.
    Unclosed tags (truncated mid-stream) are stripped from the opening
    tag to end-of-string on the assumption that everything after is
    internal reasoning the user shouldn't see.

    Args:
        text: Raw text from LLM (may be empty).

    Returns:
        Cleaned text with all reasoning tags removed and trailing
        whitespace stripped. Empty input returns empty output.
    """
    if not text:
        return text
    for pat in _THINK_PATTERNS_CLOSED:
        text = pat.sub("", text)
    for pat in _THINK_PATTERNS_UNCLOSED:
        text = pat.sub("", text)
    return text.strip()


__all__ = ["strip_thinking_tags"]