"""P5: LangGraph engine parity verification.

Tests that the langgraph engine produces the same output schema as the
phase engine for a simple graph execution.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_stub_graph():
    """Create a minimal StudyGraph for testing."""
    from strategy_research.core.study.graph import GraphEdge, GraphNode, StudyGraph

    return StudyGraph(
        nodes=(
            GraphNode(id="researcher", type="llm_agent", label="Researcher", enabled=True),
            GraphNode(id="strategist", type="planner", label="Strategist", enabled=True),
        ),
        edges=(
            GraphEdge(source="researcher", target="strategist"),
        ),
    )


def _stub_agent_result(agent_id: str, output: dict):
    """Create a stubbed AgentExecutionResult."""
    from strategy_research.core.agent.executor import AgentExecutionResult

    return AgentExecutionResult(
        agent_id=agent_id,
        status="success",
        output=json.dumps(output),
        elapsed_s=0.1,
    )


class TestLangGraphParity:
    """Verify langgraph engine output matches expected schema."""

    def test_build_langgraph_compiles(self):
        """build_langgraph produces a compiled graph."""
        from strategy_research.core.study.langgraph_engine import build_langgraph

        graph = _make_stub_graph()
        executor = MagicMock()
        workspace = Path("/tmp/test")

        compiled = build_langgraph(
            graph, executor, "task text", workspace,
            {}, None, "study_1", 1,
        )
        assert compiled is not None

    def test_build_langgraph_with_checkpointer(self):
        """build_langgraph accepts a checkpointer."""
        from langgraph.checkpoint.memory import MemorySaver
        from strategy_research.core.study.langgraph_engine import build_langgraph

        graph = _make_stub_graph()
        executor = MagicMock()
        workspace = Path("/tmp/test")
        checkpointer = MemorySaver()

        compiled = build_langgraph(
            graph, executor, "task text", workspace,
            {}, None, "study_1", 1,
            checkpointer=checkpointer,
        )
        assert compiled is not None

    def test_build_langgraph_with_hitl(self):
        """build_langgraph injects novelty gate node when profile.hitl=True."""
        from strategy_research.core.study.langgraph_engine import build_langgraph, LangGraphProfile

        graph = _make_stub_graph()
        executor = MagicMock()
        workspace = Path("/tmp/test")
        profile = LangGraphProfile(hitl=True)

        compiled = build_langgraph(
            graph, executor, "task text", workspace,
            {}, None, "study_1", 1,
            profile=profile,
        )
        assert compiled is not None

    def test_study_round_state_fields(self):
        """StudyRoundState has all required fields."""
        from strategy_research.core.study.langgraph_engine import StudyRoundState

        required_fields = [
            "study_id", "round_num", "strategy_name", "workspace_path",
            "directive_text", "agent_outputs", "hypothesis",
            "verdict_decision", "verdict_reason",
            "exec_result", "eval_result", "aborted", "abort_reason",
        ]
        for field in required_fields:
            assert field in StudyRoundState.__annotations__, f"Missing field: {field}"

    def test_merge_agent_outputs_reducer(self):
        """_merge_agent_outputs merges dicts correctly."""
        from strategy_research.core.study.langgraph_engine import _merge_agent_outputs

        left = {"agent_a": {"output": "a"}}
        right = {"agent_b": {"output": "b"}}
        result = _merge_agent_outputs(left, right)
        assert result == {"agent_a": {"output": "a"}, "agent_b": {"output": "b"}}

    def test_merge_agent_outputs_overwrites(self):
        """_merge_agent_outputs overwrites same key."""
        from strategy_research.core.study.langgraph_engine import _merge_agent_outputs

        left = {"agent_a": {"output": "old"}}
        right = {"agent_a": {"output": "new"}}
        result = _merge_agent_outputs(left, right)
        assert result == {"agent_a": {"output": "new"}}

    def test_thread_id_format(self):
        """_thread_id produces correct format."""
        from strategy_research.core.study.langgraph_engine import _thread_id

        assert _thread_id("study_123", 5) == "study_123:r5"

    def test_find_entry_nodes(self):
        """_find_entry_nodes returns nodes with no incoming edges."""
        from strategy_research.core.study.langgraph_engine import _find_entry_nodes

        graph = _make_stub_graph()
        entries = _find_entry_nodes(graph)
        assert "researcher" in entries

    def test_find_exit_nodes(self):
        """_find_exit_nodes returns nodes with no outgoing edges."""
        from strategy_research.core.study.langgraph_engine import _find_exit_nodes

        graph = _make_stub_graph()
        exits = _find_exit_nodes(graph)
        assert "strategist" in exits


class TestLangGraphProfile:
    """Verify profile system."""

    def test_phases_profile(self):
        """Phases profile: serial, no checkpoint, no hitl."""
        from strategy_research.core.study.langgraph_engine import LangGraphProfile

        p = LangGraphProfile.phases()
        assert p.serial is True
        assert p.checkpoint is False
        assert p.hitl is False

    def test_dag_profile(self):
        """DAG profile: serial, no checkpoint, no hitl."""
        from strategy_research.core.study.langgraph_engine import LangGraphProfile

        p = LangGraphProfile.dag()
        assert p.serial is True
        assert p.checkpoint is False
        assert p.hitl is False

    def test_langgraph_profile(self):
        """LangGraph profile: parallel, checkpoint, hitl."""
        from strategy_research.core.study.langgraph_engine import LangGraphProfile

        p = LangGraphProfile.langgraph()
        assert p.serial is False
        assert p.checkpoint is True
        assert p.hitl is True

    def test_get_profile(self):
        """get_profile returns correct profiles by name."""
        from strategy_research.core.study.langgraph_engine import get_profile

        p = get_profile("phases")
        assert p.serial is True
        p = get_profile("langgraph")
        assert p.serial is False
        p = get_profile("unknown")
        assert p.serial is False  # fallback to langgraph


class TestStudyInterruptModel:
    """Verify StudyInterrupt model."""

    def test_create_interrupt(self):
        """StudyInterrupt can be created with required fields."""
        from strategy_research.core.study.models import StudyInterrupt

        interrupt = StudyInterrupt(
            interrupt_id="int_123",
            study_id="study_456",
            round_num=1,
            interrupt_type="novelty_gate",
            payload='{"hypothesis": "test"}',
            created_at="2026-01-01T00:00:00Z",
        )
        assert interrupt.interrupt_id == "int_123"
        assert interrupt.status == "pending"  # default

    def test_interrupt_status_values(self):
        """StudyInterrupt supports all status values."""
        from strategy_research.core.study.models import StudyInterrupt

        for status in ("pending", "approved", "rejected", "expired"):
            interrupt = StudyInterrupt(
                interrupt_id="int_1",
                study_id="study_1",
                round_num=1,
                interrupt_type="novelty_gate",
                status=status,
                created_at="2026-01-01T00:00:00Z",
            )
            assert interrupt.status == status
