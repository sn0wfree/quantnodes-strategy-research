"""Tests for the integration module.

Tests for:
- YAML DAG loading
- Enhanced runner creation
- Enhanced scheduler creation
- Integration with existing infrastructure
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ── YAML DAG Loading Tests ─────────────────────────────────────────


class TestYamlDagLoading:
    def test_load_standard_research_dag(self):
        from strategy_research.core.study.integration import load_dag_from_yaml

        dag = load_dag_from_yaml("dags/standard_research.yaml")
        assert dag.dag_id == "standard_research"
        assert dag.name == "Standard Research Pipeline"
        assert len(dag.tasks) == 10

        # Check task dependencies
        researcher = next(t for t in dag.tasks if t.task_id == "agent_researcher")
        assert researcher.dependencies == []

        data_quality = next(t for t in dag.tasks if t.task_id == "agent_data_quality")
        assert data_quality.dependencies == ["agent_researcher"]

    def test_load_parallel_research_dag(self):
        from strategy_research.core.study.integration import load_dag_from_yaml

        dag = load_dag_from_yaml("dags/parallel_research.yaml")
        assert dag.dag_id == "parallel_research"
        assert dag.name == "Parallel Research Pipeline"

        # Check parallel groups
        data_quality = next(t for t in dag.tasks if t.task_id == "agent_data_quality")
        factor_analyst = next(t for t in dag.tasks if t.task_id == "agent_factor_analyst")

        # Both depend only on researcher
        assert data_quality.dependencies == ["agent_researcher"]
        assert factor_analyst.dependencies == ["agent_researcher"]

        # strategist depends on both
        strategist = next(t for t in dag.tasks if t.task_id == "agent_strategist")
        assert set(strategist.dependencies) == {"agent_data_quality", "agent_factor_analyst"}

    def test_load_dag_from_json(self, tmp_path):
        import json
        from strategy_research.core.study.integration import load_dag_from_json

        dag_data = {
            "dag_id": "test_json",
            "name": "Test JSON DAG",
            "tasks": [
                {
                    "task_id": "task1",
                    "task_type": "agent",
                    "name": "test_agent",
                    "config": {"agent_name": "researcher"},
                }
            ],
        }

        json_path = tmp_path / "test_dag.json"
        json_path.write_text(json.dumps(dag_data))

        dag = load_dag_from_json(json_path)
        assert dag.dag_id == "test_json"
        assert len(dag.tasks) == 1

    def test_load_dag_file_not_found(self):
        from strategy_research.core.study.integration import load_dag_from_yaml

        with pytest.raises(FileNotFoundError):
            load_dag_from_yaml("nonexistent.yaml")

    def test_load_dag_invalid_yaml(self, tmp_path):
        from strategy_research.core.study.integration import load_dag_from_yaml

        yaml_path = tmp_path / "invalid.yaml"
        yaml_path.write_text("invalid: yaml: content:")

        # This should either raise or return empty
        try:
            load_dag_from_yaml(yaml_path)
        except Exception:
            pass  # Expected


# ── Enhanced Runner Tests ──────────────────────────────────────────


class TestEnhancedRunner:
    def test_create_enhanced_runner(self, tmp_path):
        from unittest.mock import MagicMock
        from strategy_research.core.study.integration import create_enhanced_runner

        # Create mock study and store
        study = MagicMock()
        study.study_id = "test-study"
        study.workspace_path = str(tmp_path)
        study.session_id = "test-session"
        study.current_round = 0
        study.max_rounds = 10
        study.behavior = "static"

        store = MagicMock()

        # Create enhanced runner
        runner = create_enhanced_runner(study, store)

        # Check that infrastructure components are attached
        assert hasattr(runner, "_event_store")
        assert hasattr(runner, "_checkpoint_mgr")
        assert hasattr(runner, "_streaming_emitter")

    def test_create_enhanced_runner_with_dag(self, tmp_path):
        from unittest.mock import MagicMock
        from strategy_research.core.study.integration import create_enhanced_runner

        study = MagicMock()
        study.study_id = "test-study"
        study.workspace_path = str(tmp_path)
        study.session_id = "test-session"
        study.current_round = 0

        store = MagicMock()

        # Create enhanced runner with DAG
        runner = create_enhanced_runner(
            study, store,
            dag_yaml_path="dags/standard_research.yaml",
        )

        # Check that DAG scheduler is attached
        assert hasattr(runner, "_dag_scheduler")
        assert runner._dag_scheduler is not None


# ── Enhanced Scheduler Tests ───────────────────────────────────────


class TestEnhancedScheduler:
    def test_create_enhanced_scheduler(self):
        from unittest.mock import MagicMock
        from strategy_research.core.study.integration import create_enhanced_scheduler

        store = MagicMock()

        scheduler = create_enhanced_scheduler(store)

        # Check that infrastructure components are attached
        assert hasattr(scheduler, "_signal_manager")
        assert hasattr(scheduler, "_checkpoint_mgr")


# ── Integration Tests ──────────────────────────────────────────────


class TestIntegration:
    def test_run_study_with_dag(self, tmp_path):
        """Test running a study with YAML DAG configuration."""
        from unittest.mock import MagicMock, patch
        from strategy_research.core.study.integration import (
            create_enhanced_runner,
            load_dag_from_yaml,
        )

        # Load DAG
        dag = load_dag_from_yaml("dags/standard_research.yaml")
        assert dag.dag_id == "standard_research"

        # Create mock study
        study = MagicMock()
        study.study_id = "test-study"
        study.workspace_path = str(tmp_path)
        study.session_id = "test-session"
        study.current_round = 0
        study.max_rounds = 1
        study.behavior = "static"

        store = MagicMock()

        # Create enhanced runner
        runner = create_enhanced_runner(
            study, store,
            dag_yaml_path="dags/standard_research.yaml",
        )

        # Verify components
        assert runner._event_store is not None
        assert runner._checkpoint_mgr is not None
        assert runner._dag_scheduler is not None

    def test_event_store_persistence(self, tmp_path):
        """Test that events persist across runner instances."""
        from strategy_research.core.study.event_store import EventStore, EventType

        # Create event store
        db_path = tmp_path / "events.db"
        store1 = EventStore(db_path=db_path)

        # Add events
        store1.append(EventType.STUDY_CREATED, "study-1")
        store1.append(EventType.STUDY_STARTED, "study-1")

        # Create new event store (simulating restart)
        store2 = EventStore(db_path=db_path)

        # Query events
        events = store2.query("study-1")
        assert len(events) == 2
        assert events[0].event_type == EventType.STUDY_CREATED

    def test_checkpoint_persistence(self, tmp_path):
        """Test that checkpoints persist across manager instances."""
        from strategy_research.core.study.checkpoint import (
            CheckpointManager, CheckpointConfig,
        )

        # Create checkpoint manager
        config = CheckpointConfig(
            location=str(tmp_path / "checkpoints.db"),
            backend="sqlite",
        )
        mgr1 = CheckpointManager(config)

        # Save checkpoint
        mgr1.save_checkpoint("study-1", {"round": 5})

        # Create new manager (simulating restart)
        mgr2 = CheckpointManager(config)

        # Load checkpoint
        cp = mgr2.load_latest("study-1")
        assert cp is not None
        assert cp.state["round"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
