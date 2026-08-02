"""Context compaction engine — opencode-aligned L4 with safety check.

Layer (relative to threshold_tokens):
    L4 LLM Summarize (95%): structured Markdown summary via LLM

Design inspired by opencode's compaction architecture:
    - Structured summary template
    - Token budget selection (preserve_recent_tokens + tail_turns)
    - Incremental summary update
    - Single L4 layer (matches opencode `packages/core/src/session/compaction.ts`)

Phase A simplification (chore):
    - Removed L1 (smart microcompact) and L3 (hard truncate)
    - Kept deprecated fields in CompactConfig for backward compat
    - Added `simplified_to_l4_only` flag to opt-in to legacy 3-layer behavior
      (read-only; legacy code path removed; flag is controller-only)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Emergency kill switch ────────────────────────────────────────────
# Set SR_KEEP_ALL_COMPACTIONS=1 to globally force
# keep_all_compactions_in_history=True without code changes or config edits.
# Useful for emergency rollback if the new filter causes issues.
# See docs/compaction-history-filter.md.
_KEEP_ALL_COMPACTIONS_OVERRIDE: bool = os.environ.get(
    "SR_KEEP_ALL_COMPACTIONS", ""
).lower() in ("1", "true", "yes", "on")


# ── In-memory monitoring counters ────────────────────────────────────
# Lightweight metrics for observability. Not thread-safe by design
# (Python GIL makes simple int++ atomic enough for monitoring).
_compaction_metrics: dict[str, int] = {
    "total_hidden": 0,           # Total older compactions hidden from LLM
    "total_kept": 0,             # Total compactions kept in LLM
    "l4_aborts": 0,              # L4 safety aborts (would produce empty context)
    "filter_calls": 0,           # Total _convert_messages_to_history calls
}


def get_compaction_metrics() -> dict[str, int]:
    """Return a snapshot of compaction metrics (read-only copy)."""
    return dict(_compaction_metrics)


def reset_compaction_metrics() -> None:
    """Reset all compaction metrics to zero. Used by tests."""
    _compaction_metrics["total_hidden"] = 0
    _compaction_metrics["total_kept"] = 0
    _compaction_metrics["l4_aborts"] = 0
    _compaction_metrics["filter_calls"] = 0


def set_keep_all_override(enabled: bool) -> None:
    """Runtime kill switch toggle (used by admin API)."""
    global _KEEP_ALL_COMPACTIONS_OVERRIDE
    _KEEP_ALL_COMPACTIONS_OVERRIDE = enabled
    logger.warning(
        "compaction kill switch: keep_all_compactions_in_history=%s",
        enabled,
    )


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

# DEPRECATED since Phase A: was the L1 per-tool-type threshold table.
# Kept only as documentation reference for the (now-unused) tool_truncate_chars
# field default. Field default now uses an empty dict; L4 doesn't pre-truncate.
_LEGACY_L1_TOOL_LIMITS_DOC: dict[str, int] = {
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

    All defaults follow the opencode approach (packages/core/src/session/
    compaction.ts). The current implementation is L4-only (matches opencode).
    """

    enabled: bool = True

    # ── Phase A simplification toggle ─────────────────────────
    # When True (default), use opencode-aligned L4-only 2-step flow.
    # When False, fall back to legacy 3-layer (L1+L4+L3) flow.
    # (Phase A: legacy code path removed; this flag is reserved for
    # future use. Currently always True.)
    simplified_to_l4_only: bool = True

    # ── Trigger ──────────────────────────────────────────────────
    # None = derive from model context (opencode-style):
    #   trigger = context - max(model_max_output, buffer)
    # Explicit int = use as absolute threshold.
    threshold_tokens: int | None = None
    # Opencode DEFAULT_BUFFER: leave this much room for the response
    compaction_buffer_tokens: int = 20_000
    # Fallback trigger when model context is unknown
    fallback_threshold_tokens: int = 8_000

    # ── Layer ratios (relative to threshold_tokens) ──────────────
    # DEPRECATED since Phase A: L1/L3 ratios are no longer used.
    # Kept for backward compat with existing llm.json files.
    # Ignored at runtime; only llm_summarize_ratio is used.
    microcompact_ratio: float = 0.9        # DEPRECATED: was L1 ratio (0.9)
    llm_summarize_ratio: float = 0.95      # L4: 95%  (was 0.8)
    hard_truncate_ratio: float = 0.99      # DEPRECATED: was L3 ratio (0.99)
    overflow_ratio: float = 0.99          # DEPRECATED: was overflow detection (0.99)

    # ── L1: Smart Microcompact (DEPRECATED) ─────────────────────
    # DEPRECATED since Phase A: L1 layer removed.
    # Fields kept for backward compat with existing llm.json files.
    # Ignored at runtime.
    microcompact_tool_result_chars: int = 2_000  # DEPRECATED
    tool_truncate_chars: dict[str, int] = field(default_factory=dict)  # DEPRECATED
    collapse_keep_recent: int = 4         # DEPRECATED: was L3 keep_recent
    serialize_tool_max_chars: int = 2_000  # NEW: used by _serialize_message for L4 input

    # ── L4: LLM Summarize ─────────────────────────────────────────
    # Opencode formula: actual max_tokens = min(model_max_output, this)
    # i.e. this is a CAP, not the absolute value.
    preserve_recent_tokens: int | None = None  # None = dynamic (25% of context)
    tail_turns: int = 2                        # keep last N turns verbatim
    summary_output_tokens: int = 4_096          # CAP, see opencode line 183
    enable_incremental_summary: bool = True
    summary_template: str | None = None        # None = DEFAULT_SUMMARY_TEMPLATE
    # Minimum number of messages required from L4 (safety check).
    # If L4 produces fewer, the compaction is aborted to avoid
    # provider errors like MiniMax 2013 "chat content is empty".
    l4_min_messages: int = 2
    # Token estimation: avg chars per token. Used by _estimate_tokens.
    chars_per_token: float = 3.0

    # ── History projection ────────────────────────
    # When True, send ALL compaction messages in history (legacy behavior).
    # When False (default), only the MOST RECENT compaction is included
    # in LLM context. Older compactions are hidden from LLM but kept in DB
    # so the UI can still render them as audit history.
    # See docs/compaction-history-filter.md for the opencode-aligned rationale.
    keep_all_compactions_in_history: bool = False


# ── Token estimation ──────────────────────────────────────────────


def _estimate_tokens(messages: list[dict[str, Any]], chars_per_token: float = 3.0) -> int:
    """Rough token count for a list of messages (shared impl)."""
    from ..utils.token_utils import estimate_tokens
    return estimate_tokens(messages, chars_per_token=chars_per_token)


def _estimate_single_tokens(text: str, chars_per_token: float = 3.0) -> int:
    """Rough token count for a plain string (shared impl)."""
    from ..utils.token_utils import estimate_tokens_text
    return estimate_tokens_text(text, chars_per_token=chars_per_token)


# ── Message serialization (opencode-style) ───────────────────────

_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)

TOOL_OUTPUT_MAX_CHARS = 2000


def _serialize_message(msg: dict[str, Any], tool_max_chars: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    """Serialize a message for LLM summarization input.

    Format aligned with opencode core compaction serialize():
      [User]: ...
      [Assistant]: ... (text content)
      [Assistant reasoning]: ... (extracted from person thinking tags)
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

        # Extract person thinking blocks as reasoning content
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
        truncated = content[:tool_max_chars] + "\n[truncated]" if len(content) > tool_max_chars else content
        # Detect error status
        is_error = False
        if truncated.startswith("{") and '"status"' in truncated[:100]:
            try:
                import json
                parsed = json.loads(truncated[:tool_max_chars])
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


# ── L1: Smart Microcompact (DEPRECATED, removed in Phase A) ───────
# L1 was over-aggressive (truncated tool outputs before L4 sees them).
# Removed in commit A2. L4 + _serialize_message tool truncation is sufficient.
# Config fields (microcompact_tool_result_chars, tool_truncate_chars,
# collapse_keep_recent) kept for backward compat; ignored at runtime.


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
            f"{conversation}\n\n"
            f"Update the anchored summary below using the conversation history above.\n"
            f"Preserve still-true details, remove stale details, and merge in the new facts.\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            f"{template}"
        )
    return f"{conversation}\n\nCreate a new anchored summary from the conversation history.\n\n{template}"


def _llm_summarize_v2(
    messages: list[dict[str, Any]],
    config: CompactConfig,
    model_context_tokens: int | None,
    model_max_output_tokens: int | None,
    llm_client: Any,
    previous_summary: str | None = None,
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], str, str] | None:
    """L4: LLM summarization with structured template + token budget.

    Key improvements:
    1. Token budget selection (not fixed message count)
    2. Structured Markdown template (opencode-style)
    3. Incremental summary update
    4. opencode-aligned:
       - Returns 3-tuple (messages, summary_text, recent_text)
       - recent_text is pre-serialized in compact (no recompute in loop)
       - max_tokens for LLM = min(model_max_output, config.summary_output_tokens)
         (opencode line 183: Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS))
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

    # opencode formula: max_tokens = min(model_max_output, summary_output_tokens)
    # See packages/core/src/session/compaction.ts:183:
    #   const summaryOutput = Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS)
    summary_max_tokens = config.summary_output_tokens
    if model_max_output_tokens is not None:
        summary_max_tokens = min(
            model_max_output_tokens, config.summary_output_tokens,
        )

    try:
        summary_response = llm_client.chat(
            [
                {"role": "system", "content": "You are a concise conversation summarizer for a quant research agent."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=summary_max_tokens,
        )
        summary_text = summary_response.content or ""
        if not summary_text.strip():
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM summarize failed: %s", exc)
        return None

    # opencode-aligned: return (messages, summary_text, recent_text).
    # The returned messages do NOT contain an inline summary; the
    # caller persists the CompactionMessage separately and projects
    # it via to_llm_message() with <conversation-checkpoint> wrap.
    # This fixes the bug where the LLM sees the prior [context summary]
    # as a previous assistant turn and continues the summary task on
    # the next user message.
    #
    # recent_text is pre-serialized here in compact (single source of
    # truth for "what's recent"). The loop no longer recomputes it.
    recent_text = "\n\n".join(
        _serialize_message(m) for m in recent if m
    )

    # ── Safety check: ensure L4 result is usable ──
    # If new_messages is too short or has no user role, the LLM would
    # receive a context without user content — causing provider errors
    # like MiniMax 2013 "chat content is empty". Return None to let
    # the caller skip L4 and keep the original messages.
    new_messages = system_msgs + recent
    if len(new_messages) < config.l4_min_messages:
        logger.warning(
            "L4 produced too few messages (len=%d, min=%d), aborting compaction",
            len(new_messages), config.l4_min_messages,
        )
        _compaction_metrics["l4_aborts"] += 1
        return None
    if not any(m.get("role") == "user" for m in new_messages):
        logger.warning(
            "L4 produced messages without any user role, aborting compaction",
        )
        _compaction_metrics["l4_aborts"] += 1
        return None

    return new_messages, summary_text, recent_text


# ── L3: Hard Truncate (DEPRECATED, removed in Phase A) ────────────
# L3 was redundant: L4 summary + safety check with l4_min_messages
# already prevents hung-state. L3 dropped oldest messages, which
# could lose user context. Removed in commit A3.
# collapse_keep_recent config field kept (deprecated) for backward compat.


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


def _resolve_threshold_tokens(
    config: CompactConfig,
    model_context_tokens: int | None,
    model_max_output_tokens: int | None,
) -> int:
    """Derive trigger threshold from model context (opencode-aligned).

    Opencode formula (packages/core/src/session/compaction.ts:225-235):
        trigger = context - max(model_max_output, buffer)

    We extend with explicit override: if config.threshold_tokens is
    set, use it as-is. Otherwise derive. Fall back to
    ``config.fallback_threshold_tokens`` when model context is unknown.
    """
    if config.threshold_tokens is not None:
        return config.threshold_tokens
    if model_context_tokens and model_context_tokens > 0:
        buffer = config.compaction_buffer_tokens
        output = model_max_output_tokens or buffer
        trigger = model_context_tokens - max(output, buffer)
        # Guard: trigger must be reasonable (at least fallback_threshold_tokens)
        return max(config.fallback_threshold_tokens, trigger)
    return config.fallback_threshold_tokens


def compact_messages(
    messages: list[dict[str, Any]],
    config: CompactConfig | None = None,
    threshold_tokens: int | None = None,
    model_context_tokens: int | None = None,
    model_max_output_tokens: int | None = None,
    llm_client: Any | None = None,
    previous_summary: str | None = None,
    session_id: str | None = None,
    on_compaction: "CompactionCallback | None" = None,) -> tuple[list[dict[str, Any]], list[str], str | None, str | None]:
    """Apply context compression if over threshold (opencode-aligned L4-only).

    opencode-aligned (Phase A):
        - Single L4 layer (no L1/L3 pre/post processing)
        - Trigger derived from model context when threshold_tokens is None
        - Returns 4-tuple including recent_text pre-serialized by compact
        - threshold_tokens=0 still forces L4 to run (manual /compact)
        - L4 safety check (l4_min_messages) prevents hung-state

    L4 layer:
        1. Resolve threshold (opencode formula: context - max(output, buffer))
        2. Skip if under llm_summarize_ratio * threshold
        3. Run _llm_summarize_v2 with safety check
        4. Fix orphaned tool pairs (post-L4 repair)
        5. Return (messages, applied_layers, l4_summary, l4_recent)

    Args:
        messages: Conversation messages in OpenAI format.
        config: Compaction parameters (defaults to CompactConfig()).
        threshold_tokens: Absolute token budget for trigger. None =
            derive from model context via opencode formula. 0 = force L4.
        model_context_tokens: Model's context window size.
        model_max_output_tokens: Model's max output tokens (for
            summary cap formula).
        llm_client: LLM client for L4 summarization (sync .chat()).
        previous_summary: Previous summary text for incremental update.
        session_id: Session id (required for L4 to persist event).
        on_compaction: Optional callback invoked with the
            CompactionMessage after L4 generates it.
            TODO(architecture): no callers pass this today — planned
            hook point for streaming/persisting the generated
            CompactionMessage to the caller's event channel.

    Returns:
        (compressed_messages, applied_layers, l4_summary_text, l4_recent_text)
        - l4_summary_text: L4 output, or None if L4 didn't run
        - l4_recent_text: pre-serialized recent messages for the LLM's
          <recent-context> section, or None if L4 didn't run.
          Caller persists both as a CompactionMessage event.
    """
    cfg = config or CompactConfig()
    applied: list[str] = []
    l4_summary_text: str | None = None
    l4_recent_text: str | None = None

    if not cfg.enabled:
        return messages, applied, l4_summary_text, l4_recent_text

    # Resolve threshold (opencode-style derivation)
    if threshold_tokens is None:
        threshold_tokens = _resolve_threshold_tokens(
            cfg, model_context_tokens, model_max_output_tokens,
        )

    tokens = _estimate_tokens(messages)

    # threshold_tokens=0 is a sentinel meaning "force L4 to run"
    # (used by manual /compact). Bypass ratio gating in force mode.
    force_l4 = threshold_tokens == 0
    l4_threshold = 0 if force_l4 else threshold_tokens * cfg.llm_summarize_ratio

    # Early exit: below L4 threshold (skip when force_l4)
    if not force_l4 and tokens < l4_threshold:
        return messages, applied, l4_summary_text, l4_recent_text

    # ── L4: LLM Summarize (opencode-aligned single layer) ─────
    if llm_client is not None:
        old_len = len(messages)
        l4_result = _llm_summarize_v2(
            messages, cfg, model_context_tokens, model_max_output_tokens,
            llm_client, previous_summary, session_id=session_id,
        )
        if l4_result is not None:
            new_messages, summary_text, recent_text = l4_result
            if len(new_messages) < old_len and summary_text.strip():
                messages = new_messages
                applied.append(f"llm_summarize({old_len}->{len(messages)})")
                l4_summary_text = summary_text
                l4_recent_text = recent_text

    # ── Fix orphaned tool pairs (post-L4 repair) ───────────────
    pre_fix_len = len(messages)
    messages = _fix_tool_pairs(messages)
    if len(messages) < pre_fix_len:
        applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

    return messages, applied, l4_summary_text, l4_recent_text
