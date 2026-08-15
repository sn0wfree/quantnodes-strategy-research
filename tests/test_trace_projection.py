"""Tests for TraceProjection (Phase A2).

Verifies that ``GET /session/{id}/trace`` — via TraceProjection — derives
``llm_request`` records from the event_log (single source of truth) and
reconstructs large offloaded fields (system_prompt, tools_schema) from
their sidecar blobs, so a separate trace.jsonl is no longer required.
"""

from __future__ import annotations

from strategy_research.core.agent.event_store import EventStore


def _big(n: int) -> str:
    return "P" * n


class _Attempt:
    attempt_id = "att-1"
    message_id = "msg-1"
    session_id = "sess-1"


def _emit_llm_request(
    store: EventStore,
    *,
    system_prompt: str,
    tools_schema: str,
    iteration: int = 1,
) -> None:
    """Emit an llm_request through the real forwarder (so offload applies)."""
    from strategy_research.api.session.service import _LoopEventForwarder
    from strategy_research.core.llm import LLMConfig

    fwd = _LoopEventForwarder(
        service=None,
        attempt=_Attempt(),
        accumulated_parts=[],
        event_bus=store,
        cfg=LLMConfig(api_key="sk-test", model="fake-model"),
    )
    fwd("llm_request", {
        "type": "llm_request",
        "iteration": iteration,
        "session_id": "sess-1",
        "history_count": 2,
        "system_prompt_len": len(system_prompt),
        "system_prompt": system_prompt,
        "tools_schema": tools_schema,
    })


def _store(tmp_path) -> EventStore:
    return EventStore(db_path=tmp_path / "sessions.db")


def test_projection_reconstructs_offloaded_fields(tmp_path, monkeypatch) -> None:
    """Large system_prompt / tools_schema are offloaded at emit time and
    reconstructed by the projection from the sidecar blobs."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)

    big_prompt = _big(5000)
    big_tools = "[" + ",".join(f"t{i}" for i in range(3000)) + "]"
    _emit_llm_request(store, system_prompt=big_prompt, tools_schema=big_tools)

    from strategy_research.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    r = records[0]
    assert r["type"] == "llm_request"
    assert r["system_prompt"] == big_prompt
    assert r["tools_schema"] == big_tools


def test_projection_keeps_small_fields_inline(tmp_path) -> None:
    """Fields under the threshold stay inline and pass through unchanged."""
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt="short sys", tools_schema="[]")

    from strategy_research.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    r = records[0]
    assert r["system_prompt"] == "short sys"
    assert r["tools_schema"] == "[]"
    assert "system_prompt_path" not in r


def test_projection_orders_and_limits(tmp_path) -> None:
    """Records come back append-ordered and truncated to the last ``limit``."""
    store = _store(tmp_path)
    for i in range(1, 4):
        _emit_llm_request(store, system_prompt=f"sys-{i}", tools_schema="[]", iteration=i)

    from strategy_research.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1", limit=2)
    assert [r["iteration"] for r in records] == [2, 3]


def test_projection_type_filter(tmp_path, monkeypatch) -> None:
    """A ``types`` allowlist filters which event types are returned."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt=_big(5000), tools_schema="[]")
    # A non-llm_request event should be excluded by the default filter.
    store.emit("sess-1", "tool_result", {"name": "ls", "result": "ok"})

    from strategy_research.api.session.trace_projection import TraceProjection

    recs = TraceProjection(store).project("sess-1")
    assert [r["type"] for r in recs] == ["llm_request"]

    recs2 = TraceProjection(store).project("sess-1", types="tool_result")
    assert [r["type"] for r in recs2] == ["tool_result"]


def test_projection_empty_session(tmp_path) -> None:
    """A session with no events yields no records."""
    from strategy_research.api.session.trace_projection import TraceProjection

    assert TraceProjection(_store(tmp_path)).project("sess-missing") == []


def test_projection_no_offload_when_missing_blob(tmp_path, monkeypatch) -> None:
    """A dangling ``_path`` reference (blob missing) is skipped, not fatal."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt=_big(5000), tools_schema="[]")

    # Remove the sidecar blob so reconstruction has nothing to read.
    blob_dir = tmp_path / "trace-blobs"
    for f in blob_dir.glob("*.txt"):
        f.unlink()

    from strategy_research.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    assert "system_prompt" not in records[0]
