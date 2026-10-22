"""Tests for study engine dispatch + plan-dag async wiring.

Originally lived in tests/test_study_dag_engine.py together with the
dag-engine golden-output tests (now archived to attic/). Only the dispatch
-tests + plan-dag async test remain — they verify the live dispatch
contract (engine='dag' → langgraph, asyncio.to_thread planner wiring).
"""
from __future__ import annotations


class TestEngineDispatch:
    def test_env_flag_is_deprecated_and_ignored(self, monkeypatch):
        """SR_STUDY_DAG_ENGINE no longer remaps engines (final cleanup).

        Behavior assertion on _resolve_engine: the legacy switch logs a
        deprecation warning but the resolved value is unchanged.
        """
        from types import SimpleNamespace

        from strategy_research.core.study.phase_engine import _resolve_engine

        monkeypatch.setenv("SR_STUDY_DAG_ENGINE", "1")
        study = SimpleNamespace(engine="phases")
        assert _resolve_engine(study, round_num=1) == "phases"

    def test_engine_dag_passes_through_to_dispatcher_fallback(self):
        """engine='dag' is deprecated: no remap, no dag executor.

        _resolve_engine returns the value unchanged; the dispatcher
        falls back to phases with a warning (pre-Step-2 if/else
        semantics). Previously this test asserted the dag->langgraph
        remap — that fake option was removed in the final cleanup.
        """
        from types import SimpleNamespace

        from strategy_research.core.study import phase_engine

        study = SimpleNamespace(engine="dag")
        assert phase_engine._resolve_engine(study, round_num=1) == "dag"
        # dispatcher has no 'dag' key -> run_round_phases falls back to phases
        assert "dag" not in phase_engine._ENGINE_BRANCHES


class TestPlanDagAsync:
    def test_plan_dag_uses_to_thread(self):
        """study_plan_dag must use asyncio.to_thread to avoid blocking
        the event loop during the synchronous LLM call."""
        import inspect

        from strategy_research.api.routers.study import study_plan_dag
        src = inspect.getsource(study_plan_dag)
        assert "asyncio.to_thread" in src
        assert "planner.plan" in src
