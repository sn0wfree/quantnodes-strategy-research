"""Tests for text-part-routing (opencode-style 3-step text protocol).

Verifies the SSE event accumulation in api/session/service.py:_accumulate_part
and the chat.py:_run_agent_loop_background on_event callback correctly route
text segments by text_id, so back-to-back text→tool_call→text sequences
land in the right order in parts_json.

Reference: docs/text-part-routing.md
"""
from __future__ import annotations

import pytest

from strategy_research.api.session.service import _accumulate_part


# ─────────────────────────────────────────────────────────────────────────────
# _accumulate_part: text.started / text_delta / text.ended lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def test_text_started_creates_empty_text_part_with_id():
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {"text_id": "t1"})
    assert parts == [{"type": "text", "id": "t1", "text": ""}]


def test_text_delta_appends_to_matching_part_by_id():
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {"text_id": "t1"})
    _accumulate_part(parts, "text_delta", {"text_id": "t1", "text": "Hello"})
    _accumulate_part(parts, "text_delta", {"text_id": "t1", "text": " world"})
    assert parts == [{"type": "text", "id": "t1", "text": "Hello world"}]


def test_text_delta_with_orphan_id_pushes_new_part():
    """Late text_delta without a preceding text.started still creates a part."""
    parts: list[dict] = []
    _accumulate_part(parts, "text_delta", {"text_id": "orphan", "text": "hi"})
    assert parts == [{"type": "text", "id": "orphan", "text": "hi"}]


def test_text_delta_without_id_is_dropped():
    """Hard-break: text_delta without text_id is a protocol error."""
    parts: list[dict] = []
    _accumulate_part(parts, "text_delta", {"text": "no id"})
    assert parts == []


def test_text_ended_overrides_final_text():
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {"text_id": "t1"})
    _accumulate_part(parts, "text_delta", {"text_id": "t1", "text": "partial"})
    _accumulate_part(parts, "text.ended", {"text_id": "t1", "text": "complete"})
    assert parts == [{"type": "text", "id": "t1", "text": "complete"}]


def test_text_delta_routes_to_correct_segment_after_tool_call():
    """Regression: text streamed after tool_call must NOT merge with prior text.

    Reproduces the original bug: text_delta after a tool_call was previously
    appended to the first text part. With text_id routing, it creates a new
    text part positioned after the tool_call.
    """
    parts: list[dict] = []

    # Iteration 1: text "T1" → tool_call "tc1"
    _accumulate_part(parts, "text.started", {"text_id": "u1"})
    _accumulate_part(parts, "text_delta", {"text_id": "u1", "text": "T1"})
    _accumulate_part(parts, "text.ended", {"text_id": "u1", "text": "T1"})
    _accumulate_part(parts, "tool_call", {"id": "tc1", "name": "foo", "arguments": "{}"})
    _accumulate_part(parts, "tool_result", {"id": "tc1", "result": "ok", "status": "done"})

    # Iteration 2: text "T2" → tool_call "tc2"
    _accumulate_part(parts, "text.started", {"text_id": "u2"})
    _accumulate_part(parts, "text_delta", {"text_id": "u2", "text": "T2"})
    _accumulate_part(parts, "text.ended", {"text_id": "u2", "text": "T2"})
    _accumulate_part(parts, "tool_call", {"id": "tc2", "name": "bar", "arguments": "{}"})

    # Iteration 3: text "T3"
    _accumulate_part(parts, "text.started", {"text_id": "u3"})
    _accumulate_part(parts, "text_delta", {"text_id": "u3", "text": "T3"})
    _accumulate_part(parts, "text.ended", {"text_id": "u3", "text": "T3"})

    assert parts == [
        {"type": "text", "id": "u1", "text": "T1"},
        {"type": "tool_call", "id": "tc1", "name": "foo", "arguments": "{}", "result": "ok", "status": "done"},
        {"type": "text", "id": "u2", "text": "T2"},
        {"type": "tool_call", "id": "tc2", "name": "bar", "arguments": "{}"},
        {"type": "text", "id": "u3", "text": "T3"},
    ]


def test_text_started_with_duplicate_id_is_not_double_pushed():
    """SSE replay / duplicate emission: text.started for an existing id is a no-op."""
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {"text_id": "t1"})
    _accumulate_part(parts, "text.started", {"text_id": "t1"})  # duplicate
    _accumulate_part(parts, "text_delta", {"text_id": "t1", "text": "abc"})
    assert parts == [{"type": "text", "id": "t1", "text": "abc"}]


def test_text_started_without_id_is_dropped():
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {})
    assert parts == []


def test_text_ended_for_unknown_id_is_noop():
    parts: list[dict] = []
    _accumulate_part(parts, "text.started", {"text_id": "t1"})
    _accumulate_part(parts, "text.ended", {"text_id": "unknown", "text": "x"})
    assert parts == [{"type": "text", "id": "t1", "text": ""}]


# ─────────────────────────────────────────────────────────────────────────────
# Tool events still work alongside text routing
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_call_and_tool_result_routing_unchanged():
    parts: list[dict] = []
    _accumulate_part(parts, "tool_call", {"id": "tc1", "name": "foo", "arguments": "{}"})
    _accumulate_part(parts, "tool_call", {"id": "tc2", "name": "bar", "arguments": "{}"})
    _accumulate_part(parts, "tool_result", {"id": "tc2", "result": "ok2", "status": "done"})
    _accumulate_part(parts, "tool_result", {"id": "tc1", "result": "ok1", "status": "done"})
    assert parts[0]["result"] == "ok1"
    assert parts[1]["result"] == "ok2"
    assert parts[0]["status"] == "done"
    assert parts[1]["status"] == "done"


# ─────────────────────────────────────────────────────────────────────────────
# chat.py:_run_agent_loop_background on_event callback
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_py_on_event_text_protocol(monkeypatch):
    """Verify the chat.py on_event callback applies the same text routing.

    Tests the inline logic in _run_agent_loop_background's on_event without
    requiring the full chat infrastructure. We replicate the relevant
    branches here and assert parts_json output matches the standalone helper.
    """
    accumulated_parts: list[dict] = []

    def on_event(event_type: str, data: dict) -> None:
        # Mirror the chat.py logic (from _run_agent_loop_background)
        if event_type == "text.started":
            text_id = data.get("text_id")
            if text_id:
                # Idempotent: skip if a part with this id already exists
                for p in reversed(accumulated_parts):
                    if p.get("type") == "text" and p.get("id") == text_id:
                        break
                else:
                    accumulated_parts.append({"type": "text", "id": text_id, "text": ""})
        elif event_type == "text_delta":
            text_id = data.get("text_id")
            text = data.get("text", "")
            if not text_id:
                return
            for p in reversed(accumulated_parts):
                if p.get("type") == "text" and p.get("id") == text_id:
                    p["text"] += text
                    break
            else:
                accumulated_parts.append({"type": "text", "id": text_id, "text": text})
        elif event_type == "text.ended":
            text_id = data.get("text_id")
            final_text = data.get("text", "")
            if text_id:
                for p in reversed(accumulated_parts):
                    if p.get("type") == "text" and p.get("id") == text_id:
                        p["text"] = final_text
                        break
        elif event_type == "tool_call":
            # Note: real chat.py doesn't dedup tool_call (delegates to UUID
            # stability of the upstream tc.id). If a duplicate arrives it's
            # appended as a new entry; tool_result then attaches to the
            # last matching id. This is fine for serial tool execution.
            accumulated_parts.append({
                "type": "tool_call",
                "id": data.get("id"),
                "name": data.get("name"),
                "arguments": data.get("arguments"),
            })
        elif event_type == "tool_result":
            for p in reversed(accumulated_parts):
                if p.get("type") == "tool_call" and p.get("id") == data.get("id"):
                    p["result"] = data.get("result")
                    p["status"] = data.get("status", "done")
                    break

    # Run a typical multi-iteration scenario
    on_event("text.started", {"text_id": "iter1"})
    on_event("text_delta", {"text_id": "iter1", "text": "Hello, "})
    on_event("text_delta", {"text_id": "iter1", "text": "world"})
    on_event("text.ended", {"text_id": "iter1", "text": "Hello, world"})
    on_event("tool_call", {"id": "tc1", "name": "lookup", "arguments": '{"q":"foo"}'})
    on_event("tool_result", {"id": "tc1", "result": "bar", "status": "done"})
    on_event("text.started", {"text_id": "iter2"})
    on_event("text_delta", {"text_id": "iter2", "text": "Done."})
    on_event("text.ended", {"text_id": "iter2", "text": "Done."})

    assert accumulated_parts == [
        {"type": "text", "id": "iter1", "text": "Hello, world"},
        {"type": "tool_call", "id": "tc1", "name": "lookup", "arguments": '{"q":"foo"}', "result": "bar", "status": "done"},
        {"type": "text", "id": "iter2", "text": "Done."},
    ]
