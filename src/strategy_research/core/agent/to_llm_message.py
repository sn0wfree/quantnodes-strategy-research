"""Unified LLM projection layer (opencode-aligned).

TODO(architecture): module is currently NOT wired into production —
``loop.py`` still uses its own inline projection instead of this
module, despite docs/compaction-summary-fix.md designating it the
single source of truth ("Use to_llm_message, drop [context summary]
matching"). Future work (compaction Phase B wiring): have loop.py /
service history builders call ``project_to_llm_message`` /
``project_messages_to_llm`` and retire the inline projection in
``core/agent/loop.py``. Kept alive by tests (test_to_llm_message.py).

Single source of truth for "what the LLM sees" when given a list of
DB messages. Replaces the inline projection logic in `loop.py` and
provides a single function that handles all 4 message types.

Opencode reference:
  packages/core/src/session/runner/to-llm-message.ts (toLLMMessage)
  packages/core/src/session/runner/to-llm-message.ts:170 (toLLMMessages)

Key behavior:
- user  → user role
- tool  → tool role
- assistant → assistant role
- compaction → user role with <conversation-checkpoint> wrap
             (this is the bug fix: prevents LLM from continuing
              the summary task on the next user turn)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .compaction_message import (
    MESSAGE_TYPE_ASSISTANT,
    MESSAGE_TYPE_COMPACTION,
    MESSAGE_TYPE_TOOL,
    MESSAGE_TYPE_USER,
    VALID_MESSAGE_TYPES,
    CompactionMessage,
)

logger = logging.getLogger(__name__)


def infer_message_type(db_message: dict) -> str:
    """Infer message_type for old data without the column.

    This is a backward-compat fallback. New code should read the
    message_type column directly from the database.

    Order:
    1. tool_call_id present → tool
    2. role = user → user
    3. role = assistant + content matches compaction patterns → compaction
    4. default → assistant

    Compaction patterns (L4 artifacts):
    - [context summary] prefix (legacy)
    - ## Anchored Summary prefix (legacy)
    - ## Objective...## Important Details (LLM-generated summary)
    """
    tool_call_id = db_message.get("tool_call_id")
    if tool_call_id:
        return MESSAGE_TYPE_TOOL
    role = db_message.get("role", "")
    if role == MESSAGE_TYPE_USER:
        return MESSAGE_TYPE_USER
    if role == MESSAGE_TYPE_ASSISTANT:
        content = (db_message.get("content") or "").strip()
        # Legacy patterns
        if content.startswith("[context summary]") or content.startswith("## Anchored"):
            return MESSAGE_TYPE_COMPACTION
        # LLM-generated summary pattern
        if content.startswith("## Objective") or "## Important Details" in content:
            return MESSAGE_TYPE_COMPACTION
    return MESSAGE_TYPE_ASSISTANT


def get_message_type(db_message: dict) -> str:
    """Get message_type, falling back to inference for old data."""
    mt = db_message.get("message_type")
    if mt and mt in VALID_MESSAGE_TYPES:
        return mt
    return infer_message_type(db_message)


def project_to_llm_message(db_message: dict) -> Optional[dict]:
    """Project a single DB message to LLM input format.
    
    Returns None for messages that should be skipped entirely
    (e.g. system-internal events that don't belong in LLM context).
    
    Returns a dict with 'role' and 'content' (or 'content_parts')
    ready to send to the LLM API.
    """
    msg_type = get_message_type(db_message)

    if msg_type == MESSAGE_TYPE_USER:
        return {
            "role": MESSAGE_TYPE_USER,
            "content": db_message.get("content", "") or "",
        }

    if msg_type == MESSAGE_TYPE_TOOL:
        result = {
            "role": MESSAGE_TYPE_TOOL,
            "content": db_message.get("content", "") or "",
        }
        tcid = db_message.get("tool_call_id")
        if tcid:
            result["tool_call_id"] = tcid
        return result

    if msg_type == MESSAGE_TYPE_ASSISTANT:
        return {
            "role": MESSAGE_TYPE_ASSISTANT,
            "content": _extract_assistant_content(db_message),
        }

    if msg_type == MESSAGE_TYPE_COMPACTION:
        # KEY FIX: project compaction as USER role with checkpoint wrap
        comp = CompactionMessage.from_db_row(db_message)
        return comp.to_llm_message()

    return None


def project_messages_to_llm(db_messages: list[dict]) -> list[dict]:
    """Project a list of DB messages to LLM input, in order.
    
    Skips None results (e.g. internal events).
    """
    result = []
    for m in db_messages:
        projected = project_to_llm_message(m)
        if projected is not None:
            result.append(projected)
    return result


def _extract_assistant_content(db_message: dict) -> str:
    """Extract text content from assistant message parts.
    
    Supports two storage formats:
    - New: parts_json is a list of parts, each with type='text'|'tool_call' etc.
    - Legacy: content field has the text directly
    """
    # New format
    parts_raw = db_message.get("parts_json") or "[]"
    try:
        parts = json.loads(parts_raw)
        if isinstance(parts, list):
            text_parts = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
            ]
            if text_parts:
                return "\n".join(text_parts)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("parts_json parse failed: %s", exc)

    # Legacy format
    return db_message.get("content", "") or ""


# ── Public utility ──────────────────────────────────────────


def is_compaction_message(db_message: dict) -> bool:
    """True if this is a compaction event (UI/loader filter)."""
    return get_message_type(db_message) == MESSAGE_TYPE_COMPACTION


def filter_out_compactions(db_messages: list[dict]) -> list[dict]:
    """Remove compaction messages from a list (e.g. for UI display)."""
    return [m for m in db_messages if not is_compaction_message(m)]


__all__ = [
    "infer_message_type",
    "get_message_type",
    "project_to_llm_message",
    "project_messages_to_llm",
    "is_compaction_message",
    "filter_out_compactions",
]
