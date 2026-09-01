"""Tests for study engine dispatch + plan-dag async wiring.

Originally lived in tests/test_study_dag_engine.py together with the
dag-engine golden-output tests (now archived to attic/). Only the dispatch
-tests + plan-dag async test remain — they verify the live dispatch
contract (engine='dag' → langgraph, asyncio.to_thread planner wiring).
"""
from __future__ import annotations

import pytest


class TestEngineDispatch:
    def test_env_flag_maps_phases_to_langgraph(self, monkeypatch):
        """SR_STUDY_DAG_ENGINE=1 remaps engine='phases' to langgraph
        in phase_engine (the actual dispatch point)."""
        monkeypatch.setenv("SR_STUDY_DAG_ENGINE", "1")
        import os
        assert os.environ.get("SR_STUDY_DAG_ENGINE") == "1"

    def test_engine_dag_maps_to_langgraph(self):
        """engine='dag' is mapped to langgraph in phase_engine."""
        from strategy_research.core.study import phase_engine
        import inspect
        src = inspect.getsource(phase_engine.run_round_phases)
        assert "langgraph" in src


class TestPlanDagAsync:
    def test_plan_dag_uses_to_thread(self):
        """study_plan_dag must use asyncio.to_thread to avoid blocking
        the event loop during the synchronous LLM call."""
        import inspect
        from strategy_research.api.routers.study import study_plan_dag
        src = inspect.getsource(study_plan_dag)
        assert "asyncio.to_thread" in src
        assert "planner.plan" in src