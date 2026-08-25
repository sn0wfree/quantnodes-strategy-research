"""Tests for resume_round_langgraph Command(resume=True) fix (P0-3a).

Pre-fix: ``compiled.invoke(None, config=config)`` re-triggered the
interrupt node instead of consuming it, so approval could never
actually resume the graph.

Post-fix: ``compiled.invoke(Command(resume=True), config=config)``
correctly passes through the interrupt gate.

Also covers the new __interrupt__ guard in resume (returns
paused_for_approval if the graph hits another interrupt during resume).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from strategy_research.core.study.langgraph_engine import resume_round_langgraph


class _FakeCommand:
    """Minimal stand-in for langgraph.types.Command so tests don't
    need the langgraph package installed."""
    def __init__(self, resume=None):
        self.resume = resume


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path
    (ws / "strategies" / "demo").mkdir(parents=True, exist_ok=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")
    return ws


def _make_resume_env(env, *, invoke_result=None):
    """Build all the mocks needed for resume_round_langgraph."""
    sid = "study-r-1"
    run_dir = env / "study" / sid / "rounds" / "round_0001"
    run_dir.mkdir(parents=True, exist_ok=True)

    runner = MagicMock()
    study = MagicMock()
    study.study_id = sid
    study.workspace_path = str(env)
    study.strategy_name = "demo"
    study.objective = "test"
    runner._get_study.return_value = study
    runner.study_store = MagicMock()
    runner._emit = MagicMock()
    runner._build_round_task_text = MagicMock(return_value="task")
    runner._save_agent_output = MagicMock()
    runner._rebuild_phase_outputs = MagicMock(return_value={"verdict": "keep"})
    runner._plugin_registry = None
    runner._loop_strategy = None

    compiled_graph = MagicMock()
    if invoke_result is None:
        invoke_result = {"agent_outputs": {}, "verdict": "keep"}
    compiled_graph.invoke.return_value = invoke_result

    return runner, compiled_graph, sid, run_dir


# ── Tests ───────────────────────────────────────────────────


@patch("strategy_research.core.study.langgraph_engine.build_langgraph")
def test_resume_passes_command_resume_true(mock_build, env):
    """compiled.invoke must receive Command(resume=True), not None."""
    runner, compiled, sid, run_dir = _make_resume_env(env)
    mock_build.return_value = compiled

    # Mock _get_checkpointer to return a checkpointer (needed to skip fallback)
    runner.study_store.get_checkpoint_conn = MagicMock()

    with patch(
        "strategy_research.core.study.langgraph_engine._get_checkpointer",
        return_value=MagicMock(),
    ):
        resume_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=run_dir,
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    # Check what was passed to compiled.invoke
    assert compiled.invoke.call_count == 1
    args, kwargs = compiled.invoke.call_args
    first_arg = args[0]

    # The first positional arg must be Command(resume=True), not None
    assert first_arg is not None, "compiled.invoke received None — interrupt would re-trigger"
    assert hasattr(first_arg, "resume"), (
        f"first arg should be Command, got {type(first_arg)}"
    )
    assert first_arg.resume is True, (
        f"Command.resume should be True, got {first_arg.resume!r}"
    )


@patch("strategy_research.core.study.langgraph_engine.build_langgraph")
def test_resume_returns_paused_if_another_interrupt(mock_build, env):
    """If graph hits another interrupt during resume, return
    paused_for_approval=True so the runner loop can re-poll."""
    runner, compiled, sid, run_dir = _make_resume_env(
        env,
        invoke_result={"__interrupt__": [MagicMock(value={})]},
    )
    mock_build.return_value = compiled

    with patch(
        "strategy_research.core.study.langgraph_engine._get_checkpointer",
        return_value=MagicMock(),
    ):
        result = resume_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=run_dir,
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    assert result["paused_for_approval"] is True
    # Resume re-interrupt doesn't create a new DB record (the
    # runner loop will handle it via _wait_for_approval).
    assert result.get("round") == 1


@patch("strategy_research.core.study.langgraph_engine.build_langgraph")
def test_resume_normal_result_saves_agent_outputs(mock_build, env):
    """On successful resume, agent outputs are saved."""
    runner, compiled, sid, run_dir = _make_resume_env(
        env,
        invoke_result={"agent_outputs": {"researcher": {"h": 1}}, "verdict": "keep"},
    )
    mock_build.return_value = compiled

    with patch(
        "strategy_research.core.study.langgraph_engine._get_checkpointer",
        return_value=MagicMock(),
    ):
        result = resume_round_langgraph(
            runner=runner,
            path=env,
            strategy="demo",
            current_state={},
            run_dir=run_dir,
            graph=MagicMock(),
            session=sid,
            sid=sid,
            round_num=1,
            directive_text=None,
        )

    assert result["verdict"] == "keep"
    runner._save_agent_output.assert_called()


@patch("strategy_research.core.study.langgraph_engine.build_langgraph")
def test_resume_calls_compiler_with_correct_thread_id(mock_build, env):
    """Resume uses the same thread_id pattern as the initial run."""
    runner, compiled, sid, run_dir = _make_resume_env(env)
    mock_build.return_value = compiled

    with patch(
        "strategy_research.core.study.langgraph_engine._get_checkpointer",
        return_value=MagicMock(),
    ):
        resume_round_langgraph(
            runner=runner, path=env, strategy="demo",
            current_state={}, run_dir=run_dir,
            graph=MagicMock(), session=sid, sid=sid,
            round_num=3, directive_text=None,
        )

    # Verify the thread_id pattern
    args, kwargs = compiled.invoke.call_args
    config = kwargs.get("config", args[1] if len(args) > 1 else None)
    assert config is not None
    thread_id = config.get("configurable", {}).get("thread_id", "")
    assert thread_id == f"{sid}:r3", f"thread_id mismatch: {thread_id}"