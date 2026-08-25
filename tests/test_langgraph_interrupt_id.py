"""Tests for langgraph_engine HITL interrupt_id capture (P0-3a fix).

Pre-fix: ``run_round_langgraph`` returned ``{"paused_for_approval": True}``
without the interrupt_id, so the frontend's InterruptApprovalCard
synthesised ``"pending:{studyId}:{round}"`` which never matched any
DB row → backend returned 404.

Post-fix: ``create_interrupt`` return value is captured and included
in the result dict as ``interrupt_id``.

These tests exercise the interrupt-handling branch with a mock graph
to avoid pulling in the full langgraph compile/run pipeline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.study.langgraph_engine import run_round_langgraph


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    return tmp_path


def _make_runner_with_interrupt(env, interrupt_payload: dict | None = None):
    """Build a runner that:
    - has a study record (actual study_id from create_study)
    - has a working study_store (real DB)
    - emits ``__interrupt__`` from a mocked compiled graph
    """
    (env / "strategies" / "demo").mkdir(parents=True, exist_ok=True)
    (env / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")

    # Real study_store that can persist the interrupt
    from strategy_research.core.study.store import StudyStore
    store = StudyStore()
    store.create_study(
        owner_session_id="tester", goal_id=None, objective="o",
        workspace_path=str(env), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    # Use the auto-generated study_id (create_study generates study_<hex>)
    row = store._conn.execute("SELECT study_id FROM studies LIMIT 1").fetchone()
    sid = row["study_id"]
    run_dir = env / "study" / sid / "rounds" / "round_0001"
    run_dir.mkdir(parents=True, exist_ok=True)

    study = MagicMock()
    study.study_id = sid
    study.session_id = sid
    study.workspace_path = str(env)
    study.strategy_name = "demo"
    study.objective = "test objective"
    study.metric_targets = []
    study.budget_turn = 100
    study.budget_time_seconds = 3600

    runner = MagicMock()
    runner._get_study.return_value = study
    runner.study_store = store
    runner._emit = MagicMock()
    runner._build_round_task_text = MagicMock(return_value="task")
    runner._loop_strategy = None
    runner._plugin_registry = None

    # Mock the compiled graph to return __interrupt__
    class _FakeInterrupt:
        def __init__(self, value):
            self.value = value

    payload = interrupt_payload or {"type": "novelty_gate", "hypothesis": "h"}
    compiled = MagicMock()
    compiled.invoke.return_value = {"__interrupt__": [_FakeInterrupt(payload)]}

    return runner, compiled, sid, store


def test_paused_result_includes_real_interrupt_id(env):
    """The paused_for_approval dict must carry an interrupt_id matching
    a DB row created by create_interrupt (not a synthesised placeholder)."""
    runner, compiled, sid, store = _make_runner_with_interrupt(env)

    # Stub build_langgraph to return our mock compiled graph
    with patch(
        "strategy_research.core.study.langgraph_engine.build_langgraph",
        return_value=compiled,
    ), patch(
        "strategy_research.core.study.langgraph_engine.save_agent_outputs"
    ):
        result = run_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=env / "study" / sid / "rounds" / "round_0001",
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    assert result["paused_for_approval"] is True
    assert result["round"] == 1
    assert result["study_id"] == sid

    # The critical assertion: interrupt_id is a real DB id, not "pending:..." or ""
    iid = result.get("interrupt_id")
    assert iid, "interrupt_id missing from paused result"
    assert isinstance(iid, str) and len(iid) > 8, (
        f"interrupt_id looks synthetic: {iid!r}"
    )
    assert not iid.startswith("pending:"), (
        "interrupt_id looks like the old synthesised 'pending:{studyId}:{round}' pattern"
    )

    # And the row actually exists in the DB
    row = store._conn.execute(
        "SELECT study_id, round_num, status, interrupt_type FROM study_interrupts "
        "WHERE interrupt_id = ?", (iid,),
    ).fetchone()
    assert row is not None, "interrupt_id not persisted to study_interrupts"
    assert row["status"] == "pending"
    assert row["interrupt_type"] == "novelty_gate"


def test_paused_result_interrupt_id_uses_payload_type(env):
    """interrupt_type comes from payload.type when provided, else novelty_gate."""
    runner, compiled, sid, store = _make_runner_with_interrupt(
        env,
        interrupt_payload={"type": "custom_gate"},
    )

    with patch(
        "strategy_research.core.study.langgraph_engine.build_langgraph",
        return_value=compiled,
    ), patch(
        "strategy_research.core.study.langgraph_engine.save_agent_outputs"
    ):
        result = run_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=env / "study" / sid / "rounds" / "round_0001",
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    iid = result["interrupt_id"]
    row = store._conn.execute(
        "SELECT interrupt_type FROM study_interrupts WHERE interrupt_id = ?",
        (iid,),
    ).fetchone()
    assert row["interrupt_type"] == "custom_gate"


def test_paused_result_falls_back_to_novelty_gate_when_payload_has_no_type(env):
    """When interrupt payload has no 'type' key, default to novelty_gate."""
    runner, compiled, sid, store = _make_runner_with_interrupt(
        env,
        interrupt_payload={"hypothesis": "h"},  # no type key
    )

    with patch(
        "strategy_research.core.study.langgraph_engine.build_langgraph",
        return_value=compiled,
    ), patch(
        "strategy_research.core.study.langgraph_engine.save_agent_outputs"
    ):
        result = run_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=env / "study" / sid / "rounds" / "round_0001",
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    iid = result["interrupt_id"]
    row = store._conn.execute(
        "SELECT interrupt_type FROM study_interrupts WHERE interrupt_id = ?",
        (iid,),
    ).fetchone()
    assert row["interrupt_type"] == "novelty_gate"


def test_multiple_pauses_create_distinct_interrupt_ids(env):
    """Two consecutive pause events must produce different interrupt_ids."""
    # First pause
    runner1, compiled1, sid, store = _make_runner_with_interrupt(env)

    with patch(
        "strategy_research.core.study.langgraph_engine.build_langgraph",
        return_value=compiled1,
    ), patch(
        "strategy_research.core.study.langgraph_engine.save_agent_outputs"
    ):
        result1 = run_round_langgraph(
            runner=runner1, path=env, strategy="demo", current_state={},
            run_dir=env / "study" / sid / "rounds" / "round_0001",
            graph=MagicMock(), session=sid, sid=sid, round_num=1,
            directive_text=None,
        )

    # Second pause (new compiled graph, same sid/round → different id since unique)
    runner2, compiled2, sid2, _ = _make_runner_with_interrupt(env)
    runner2.study_store = store  # share the same store
    with patch(
        "strategy_research.core.study.langgraph_engine.build_langgraph",
        return_value=compiled2,
    ), patch(
        "strategy_research.core.study.langgraph_engine.save_agent_outputs"
    ):
        result2 = run_round_langgraph(
            runner=runner2, path=env, strategy="demo", current_state={},
            run_dir=env / "study" / sid / "rounds" / "round_0001",
            graph=MagicMock(), session=sid, sid=sid, round_num=1,
            directive_text=None,
        )

    iid1, iid2 = result1["interrupt_id"], result2["interrupt_id"]
    assert iid1 != iid2, "two interrupts should produce distinct ids"

    # Both rows exist in the DB
    n = store._conn.execute(
        "SELECT COUNT(*) AS c FROM study_interrupts WHERE study_id = ? AND round_num = 1",
        (sid,),
    ).fetchone()["c"]
    assert n == 2