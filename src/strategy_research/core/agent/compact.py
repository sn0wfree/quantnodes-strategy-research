"""Context compaction engine — 3-layer progressive compression.

Layers (relative to threshold_tokens):
    L1 Smart Microcompact (50%): truncate oversized tool outputs
    L4 LLM Summarize (80%): structured Markdown summary via LLM
    L3 Hard Truncate (90%): keep only system + last N messages

Design inspired by opencode's compaction architecture:
    - Structured summary template
    - Token budget selection (preserve_recent_tokens + tail_turns)
    - Incremental summary update
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Structured summary template (opencode-style) ─────────────────

DEFAULT_SUMMARY_TEMPLATE = """\
Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. \
Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- "[Assistant reasoning]" blocks are internal thought process — extract only final decisions and conclusions, not the deliberation itself."""


# ── Config ────────────────────────────────────────────────────────

_DEFAULT_TOOL_LIMITS: dict[str, int] = {
    "read_file": 3000,
    "search_code": 2000,
    "run_command": 2000,
    "backtest_run": 4000,
    "list_files": 1000,
    "_default": 2000,
}


@dataclass(frozen=True)
class CompactConfig:
    """All tunable compaction parameters.

    Configurable via ``~/.quantnodes/llm.json`` ``"compact"`` section.
    """

    enabled: bool = True

    # ── Thresholds (relative to threshold_tokens) ──────────────────
    microcompact_ratio: float = 0.5       # L1: 50%
    llm_summarize_ratio: float = 0.8      # L4: 80%
    hard_truncate_ratio: float = 0.9      # L3: 90%
    overflow_ratio: float = 0.95          # overflow detection

    # ── L1: Smart Microcompact ─────────────────────────────────────
    microcompact_tool_result_limit: int = 2000
    tool_truncate_limits: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_TOOL_LIMITS))
    collapse_keep_recent: int = 4         # protect last N tool outputs

    # ── L4: LLM Summarize ──────────────────────────────────────────
    preserve_recent_tokens: int | None = None  # None = dynamic (25% of context)
    tail_turns: int = 2                        # keep last N turns verbatim
    summary_output_tokens: int = 4096
    enable_incremental_summary: bool = True
    summary_template: str | None = None        # None = DEFAULT_SUMMARY_TEMPLATE


# ── Token estimation ──────────────────────────────────────────────

_CHARS_PER_TOKEN = 3.0


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count for a list of messages."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total_chars += len(content)
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                import json
                total_chars += len(json.dumps(fn.get("arguments", "")))
        if msg.get("role") == "tool":
            total_chars += 100
    return max(1, int(total_chars / _CHARS_PER_TOKEN))


def _estimate_single_tokens(text: str) -> int:
    """Rough token count for a plain string."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# ── Message serialization (opencode-style) ───────────────────────

_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)

TOOL_OUTPUT_MAX_CHARS = 2000


def _serialize_message(msg: dict[str, Any]) -> str:
    """Serialize a message for LLM summarization input.

    Format aligned with opencode core compaction serialize():
      [User]: ...
      [Assistant]: ... (text content)
      [Assistant reasoning]: ... (extracted from <think> tags)
      [Assistant tool call]: name(input)
      [Tool result]: ... (truncated)
      [Tool error]: ...
      [System update]: ...
    """
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""

    if role == "user":
        return f"[User]: {content}"

    if role == "system":
        return f"[System update]: {content}"

    if role == "assistant":
        parts: list[str] = []

        # Extract <think> blocks as reasoning content
        if content:
            think_matches = _THINK_RE.findall(content)
            if think_matches:
                reasoning_text = "\n".join(m.strip() for m in think_matches if m.strip())
                if reasoning_text:
                    parts.append(f"[Assistant reasoning]: {reasoning_text}")
            # Remove think blocks from main content
            clean_content = _THINK_RE.sub("", content).strip()
            if clean_content:
                parts.append(f"[Assistant]: {clean_content}")

        # Tool calls
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "")
                if isinstance(args_str, str) and len(args_str) > 100:
                    args_str = args_str[:100] + "..."
                elif not isinstance(args_str, str):
                    args_str = str(args_str)[:100]
                parts.append(f"[Assistant tool call]: {fn.get('name', '?')}({args_str})")
        return "\n".join(parts)

    if role == "tool":
        truncated = content[:TOOL_OUTPUT_MAX_CHARS] + "\n[truncated]" if len(content) > TOOL_OUTPUT_MAX_CHARS else content
        # Detect error status
        is_error = False
        if truncated.startswith("{") and '"status"' in truncated[:100]:
            try:
                import json
                parsed = json.loads(truncated[:TOOL_OUTPUT_MAX_CHARS])
                if isinstance(parsed, dict) and parsed.get("status") == "error":
                    is_error = True
                    error_msg = parsed.get("message") or parsed.get("error") or str(parsed)
                    truncated = error_msg
            except Exception:
                pass
        if is_error:
            return f"[Tool error]: {truncated}"
        return f"[Tool result]: {truncated}"

    return ""


# ── L1: Smart Microcompact ───────────────────────────────────────

def _get_tool_name(messages: list[dict[str, Any]], tool_msg_index: int) -> str:
    """Find the tool name for a tool result by scanning backwards for the matching assistant tool_call."""
    tool_call_id = messages[tool_msg_index].get("tool_call_id")
    if not tool_call_id:
        return "_default"
    for j in range(tool_msg_index - 1, -1, -1):
        msg = messages[j]
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    # id is at top level of tool_call, not inside function
                    if tc.get("id") == tool_call_id:
                        fn = tc.get("function", {})
                        return fn.get("name", "_default")
    return "_default"


def _smart_microcompact(
    messages: list[dict[str, Any]],
    config: CompactConfig,
) -> tuple[list[dict[str, Any]], int]:
    """L1: Smart tool output truncation.

    Improvements over naive truncation:
    1. Per-tool-type thresholds
    2. Keep head 60% + tail 40% (tail often has results)
    3. Skip error messages (preserve for debugging)
    4. Skip recent N tool outputs (protected)
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    # Protect only the last N tool outputs when there are MORE than N total
    if config.collapse_keep_recent > 0 and len(tool_indices) > config.collapse_keep_recent:
        protected = set(tool_indices[-config.collapse_keep_recent:])
    else:
        protected = set()

    count = 0
    for i in tool_indices:
        if i in protected:
            continue
        msg = messages[i]
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue

        # Skip error messages
        if "error" in content.lower()[:200]:
            continue

        # Per-tool limit
        tool_name = _get_tool_name(messages, i)
        tool_limit = config.tool_truncate_limits.get(
            tool_name, config.microcompact_tool_result_limit,
        )

        if len(content) > tool_limit:
            head = int(tool_limit * 0.6)
            tail = tool_limit - head
            truncated = (
                content[:head]
                + f"\n... [{len(content) - tool_limit} chars truncated] ...\n"
                + content[-tail:]
            )
            messages[i] = {**msg, "content": truncated}
            count += 1

    return messages, count


# ── L4: LLM Summarize (structured template + token budget) ────────

def _split_into_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split non-system messages into user→assistant turns."""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in messages:
        current.append(msg)
        if msg.get("role") == "assistant":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _select_by_token_budget(
    non_system: list[dict[str, Any]],
    config: CompactConfig,
    model_context_tokens: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select messages: head (for summarization) vs recent (keep verbatim).

    Opencode approach:
    - tail_turns: keep last N turns verbatim
    - preserve_recent_tokens: token budget for recent messages
    """
    turns = _split_into_turns(non_system)

    if not turns:
        return non_system, []

    # Keep last tail_turns turns verbatim
    tail_turns_list = turns[-config.tail_turns:] if config.tail_turns > 0 and len(turns) > config.tail_turns else []

    # Calculate preserve_recent_tokens budget
    if config.preserve_recent_tokens is not None:
        budget = config.preserve_recent_tokens
    elif model_context_tokens is not None:
        budget = min(8000, max(2000, int(model_context_tokens * 0.20)))
    else:
        budget = 4000

    # Fill budget from recent turns backwards
    recent_msgs: list[dict[str, Any]] = []
    total_tokens = 0
    for turn in reversed(tail_turns_list):
        turn_tokens = _estimate_tokens(turn)
        if total_tokens + turn_tokens <= budget:
            recent_msgs.extend(turn)
            total_tokens += turn_tokens
        else:
            break

    recent_msgs.reverse()

    # Head = everything not in recent
    recent_ids = {id(m) for m in recent_msgs}
    head = [m for m in non_system if id(m) not in recent_ids]

    return head, recent_msgs


def _build_summary_prompt(
    conversation: str,
    previous_summary: str | None,
    template: str,
) -> str:
    """Build prompt for LLM summary with incremental update support."""
    if previous_summary:
        return (
            f"Update the anchored summary below using the conversation history above.\n"
            f"Preserve still-true details, remove stale details, and merge in the new facts.\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            f"{template}"
        )
    return f"Create a new anchored summary from the conversation history.\n\n{template}"


def _llm_summarize_v2(
    messages: list[dict[str, Any]],
    config: CompactConfig,
    model_context_tokens: int | None,
    llm_client: Any,
    previous_summary: str | None = None,
) -> list[dict[str, Any]] | None:
    """L4: LLM summarization with structured template + token budget.

    Key improvements:
    1. Token budget selection (not fixed message count)
    2. Structured Markdown template (opencode-style)
    3. Incremental summary update
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= config.tail_turns:
        return None

    # Token budget selection
    head, recent = _select_by_token_budget(non_system, config, model_context_tokens)

    if not head:
        return None

    # Serialize messages for LLM input
    serialized = [_serialize_message(m) for m in head]
    conversation = "\n\n".join(s for s in serialized if s)

    if not conversation.strip():
        return None

    # Build prompt with incremental update support
    template = config.summary_template or DEFAULT_SUMMARY_TEMPLATE
    prompt = _build_summary_prompt(conversation, previous_summary, template)

    try:
        summary_response = llm_client.chat(
            [
                {"role": "system", "content": "You are a concise conversation summarizer for a quant research agent."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=config.summary_output_tokens,
        )
        summary_text = summary_response.content or ""
        if not summary_text.strip():
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM summarize failed: %s", exc)
        return None

    summary_msg = {"role": "assistant", "content": f"[context summary]\n{summary_text}"}
    return system_msgs + [summary_msg] + recent


# ── L3: Hard Truncate ────────────────────────────────────────────

def _hard_truncate(
    messages: list[dict[str, Any]],
    keep_recent: int,
) -> list[dict[str, Any]]:
    """L3: Keep only system + last keep_recent messages."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    return system_msgs + non_system[-keep_recent:]


# ── Tool pair repair ─────────────────────────────────────────────

def _fix_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair orphaned tool_call / tool_result pairs after compression."""
    assistant_call_ids: set[str] = set()
    result_ids: set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    assistant_call_ids.add(tc_id)
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                result_ids.add(tc_id)

    orphan_results = result_ids - assistant_call_ids
    orphan_calls = assistant_call_ids - result_ids

    if not orphan_results and not orphan_calls:
        return messages

    logger.debug(
        "fix_tool_pairs: removing %d orphan results, %d orphan calls",
        len(orphan_results), len(orphan_calls),
    )

    fixed: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id in orphan_results:
                continue
            fixed.append(msg)
            continue

        if role == "assistant" and orphan_calls:
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                cleaned = [tc for tc in tool_calls if not (tc.get("id") in orphan_calls if isinstance(tc, dict) else False)]
                if cleaned:
                    msg = dict(msg, tool_calls=cleaned)
                elif not msg.get("content"):
                    continue

        fixed.append(msg)

    return fixed


# ── Main entry point ─────────────────────────────────────────────

def compact_messages(
    messages: list[dict[str, Any]],
    config: CompactConfig | None = None,
    threshold_tokens: int = 8000,
    model_context_tokens: int | None = None,
    llm_client: Any | None = None,
    previous_summary: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply context compression if over threshold.

    3-layer progressive compression:
        L1 (50%): Smart microcompact — truncate oversized tool outputs
        L4 (80%): LLM summarize — structured Markdown summary
        L3 (90%): Hard truncate — keep only system + last N messages

    Args:
        messages: Conversation messages in OpenAI format.
        config: Compaction parameters (defaults to CompactConfig()).
        threshold_tokens: Token budget for threshold calculation.
        model_context_tokens: Model's context window size (for dynamic budget).
        llm_client: LLM client for L4 summarization (sync .chat() method).
        previous_summary: Previous summary text for incremental update.

    Returns:
        (compressed_messages, applied_layers)
    """
    cfg = config or CompactConfig()
    applied: list[str] = []

    if not cfg.enabled:
        return messages, applied

    tokens = _estimate_tokens(messages)

    # threshold_tokens=0 is a sentinel meaning "force every layer to run"
    # (used by manual /compact). The downstream comparisons multiply by
    # threshold_tokens, so 0 would make "skip below L1" meaningless and
    # "run L4" always true. We bypass ratio gating in force mode.
    force_all = threshold_tokens == 0
    l1_threshold = 0 if force_all else threshold_tokens * cfg.microcompact_ratio
    l4_threshold = 0 if force_all else threshold_tokens * cfg.llm_summarize_ratio
    l3_threshold = 0 if force_all else threshold_tokens * cfg.hard_truncate_ratio

    # Early exit: below L1 threshold (skip when force_all)
    if not force_all and tokens < l1_threshold:
        return messages, applied

    # ── L1: Smart Microcompact (50%) ──────────────────────────────
    if force_all or tokens >= l1_threshold:
        messages, l1_count = _smart_microcompact(messages, cfg)
        if l1_count:
            applied.append(f"microcompact({l1_count})")

    tokens = _estimate_tokens(messages)

    # ── L4: LLM Summarize (80%) ───────────────────────────────────
    if (force_all or tokens >= l4_threshold) and llm_client is not None:
        old_len = len(messages)
        summarized = _llm_summarize_v2(
            messages, cfg, model_context_tokens, llm_client, previous_summary,
        )
        if summarized is not None and len(summarized) < old_len:
            messages = summarized
            applied.append(f"llm_summarize({old_len}->{len(messages)})")

    # ── L3: Hard Truncate (90%) ───────────────────────────────────
    tokens = _estimate_tokens(messages)
    if force_all or tokens >= l3_threshold:
        old_len = len(messages)
        messages = _hard_truncate(messages, cfg.collapse_keep_recent)
        if len(messages) < old_len:
            applied.append(f"truncate({old_len}->{len(messages)})")

    # ── Fix orphaned tool pairs ────────────────────────────────────
    pre_fix_len = len(messages)
    messages = _fix_tool_pairs(messages)
    if len(messages) < pre_fix_len:
        applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

    return messages, applied
