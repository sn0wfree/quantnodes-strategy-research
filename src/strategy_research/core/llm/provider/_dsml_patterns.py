"""DSML pseudo-tool-call pattern (shared between SiliconFlow + DeepSeek).

DeepSeek-V4-Flash (and similar reasoning models served via SiliconFlow
or the official DeepSeek API) sometimes leaks pseudo-tool-call markup
into ``reasoning_content`` / ``content`` to *express intent* to call a
tool. The real tool call travels through ``delta.tool_calls`` in a
separate stream, so these in-text blocks are pure UI noise.

Shapes observed in production (verified against the local SQLite
``event_log`` of past sessions — see ``d8d58926-...`` session):

* Standard XML: ``<tools>...</tools>``, ``<tool>...</tool>``
  (greedy match across the whole block).
* DSML bracket / pipe variants (emitted as *individual tokens* during
  streaming, so a single chunk is often just one of these):
  - ``<|DSML|tool_calls>``
  - ``<|DSML|invoke name="list_files">``
  - ``<|DSML|parameter name="workspace" string="true">...<|DSML|parameter>``
  - ``<|DSML|tool_calls>...<``  (unclosed)
  - ``[DSML | tool_calls>...<]``
  - ``｜DSML｜``  (full-width pipes — confirmed in ``event_log``)
* Unclosed blocks: model started a block but the SSE stream ended
  before the closing tag. Drop everything from the opening tag.

Used by :class:`SiliconFlowAdapter` and :class:`DeepSeekAdapter` —
NOT applied for other providers (OpenAI / Qwen / Kimi / MiniMax),
which default to passthrough via the base implementation.
"""
from __future__ import annotations

import re

# Closed ``<tools>...</tools>`` (or ``<tool>...</tool>``) block.
_DSML_BLOCK_RE = re.compile(
    r"<\s*tools?\s*>[\s\S]*?<\s*/\s*tools?\s*>",
    re.IGNORECASE,
)

# Single DSML token variants — each matches as a standalone token
# (no need to find a closing tag). Combined into one alternation so
# the stripper can ``re.sub`` them all in one pass.
_DSML_TOKEN_RE = re.compile(
    r"<\|DSML\|[^<>]*>"            # <|DSML|tool_calls>, <|DSML|parameter name="...">
    r"|\uFF5CDSML\uFF5C"           # ｜DSML｜ (full-width pipes)
    r"|\|DSML\|"                   # |DSML| bare
    r"|\[DSML\s*\|[^\]]*?\]"       # [DSML | tool_calls>...] (legacy)
)

# Opening tokens for unclosed-block truncation.
_DSML_OPEN_RE = re.compile(
    r"<\s*tools?\s*>"
    r"|<\|DSML\|"
    r"|\uFF5CDSML\uFF5C"
    r"|\|DSML\|"
    r"|\[DSML\s*\|",
    re.IGNORECASE,
)


def strip_dsml_text(text: str, strip_edges: bool = True) -> str:
    """Strip DSML pseudo-tool-call markup from a single text fragment.

    Two-pass strategy:

    1. **Closed blocks + single DSML tokens** — the bulk of leakage
       looks like ``<tools>...</tools>`` (greedy non-greedy match
       across a whole block) or a standalone ``<|DSML|tool_calls>``
       token. Both are removed in a single ``re.sub`` call.
    2. **Unclosed openings** — if step 1 left text that still starts a
       block but the stream ended mid-block (e.g. ``"...prefix
       <tools>half-written"``), truncate at the opening position so
       we never leak a half-baked block to the user.

    Idempotent — running twice has no additional effect.

    Args:
        strip_edges: when True (message path) trim the result; when
            False (streaming delta path) keep chunk boundary whitespace
            so BPE leading spaces survive (see ``normalize_thinking``).
    """
    if not text:
        return text
    # 1. Drop every closed block + standalone DSML token.
    cleaned = _DSML_BLOCK_RE.sub("", text)
    cleaned = _DSML_TOKEN_RE.sub("", cleaned)
    # 2. Defensive: if any opening tag still survives (unclosed block
    # the model started before the stream ended), drop from there on.
    m = _DSML_OPEN_RE.search(cleaned)
    if m:
        cleaned = cleaned[: m.start()]
    if strip_edges:
        cleaned = cleaned.strip()
    return cleaned
