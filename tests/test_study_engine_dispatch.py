"""Tests for study engine dispatch + plan-dag async wiring.

Originally lived in tests/test_study_dag_engine.py together with the
dag-engine golden-output tests (now archived to attic/). Only the dispatch
-tests + plan-dag async test remain — they verify the live dispatch
contract (engine='dag' → langgraph, asyncio.to_thread planner wiring).
"""
from __future__ import annotations


class TestEngineDispatch:
    def test_env_flag_maps_phases_to_langgraph(self, monkeypatch):
        """SR_STUDY_DAG_ENGINE=1 remaps engine='phases' to langgraph.

        Behavior assertion on _resolve_engine (Step 2 moved the inline
        remap out of run_round_phases; the previous version only
        checked that setenv round-tripped and never touched phase_engine).
        """
        from types import SimpleNamespace

        from strategy_research.core.study.phase_engine import _resolve_engine

        monkeypatch.setenv("SR_STUDY_DAG_ENGINE", "1")
        study = SimpleNamespace(engine="phases")
        assert _resolve_engine(study, round_num=1) == "langgraph"

    def test_engine_dag_maps_to_langgraph(self):
        """engine='dag' is mapped to langgraph (no dag executor exists).

        Behavior assertion on _resolve_engine — the earlier version was
        a source-string check ('langgraph' in inspect.getsource of
        run_round_phases) that pinned WHERE the remap lived rather than
        that it works.
        """
        from types import SimpleNamespace

        from strategy_research.core.study.phase_engine import _resolve_engine

        study = SimpleNamespace(engine="dag")
        assert _resolve_engine(study, round_num=1) == "langgraph"


class TestPlanDagAsync:
    def test_plan_dag_uses_to_thread(self):
        """study_plan_dag must use asyncio.to_thread to avoid blocking
        the event loop during the synchronous LLM call."""
        import inspect

        from strategy_research.api.routers.study import study_plan_dag
        src = inspect.getsource(study_plan_dag)
        assert "asyncio.to_thread" in src
        assert "planner.plan" in src
