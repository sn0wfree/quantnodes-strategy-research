"""Tests for the new study infrastructure modules.

Tests for:
- Event Sourcing (event_store.py)
- Activity Isolation (activity.py)
- Signal/Timer (signals.py)
- Checkpoint (checkpoint.py)
- Streaming (streaming.py)
- DAG Orchestration (dag.py)
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

# ── Event Store Tests ──────────────────────────────────────────────


class TestEventStore:
    def test_append_and_query(self, tmp_path):
        from strategy_research.core.study.event_store import EventStore, EventType

        store = EventStore(db_path=tmp_path / "events.db")

        # Append events
        store.append(EventType.STUDY_CREATED, "study-1", {"name": "test"})
        store.append(EventType.STUDY_STARTED, "study-1", {"round": 1})
        store.append(EventType.ROUND_COMPLETED, "study-1", {"round": 1, "verdict": "keep"})

        # Query events
        events = store.query("study-1")
        assert len(events) == 3
        assert events[0].event_type == EventType.STUDY_CREATED
        assert events[2].event_type == EventType.ROUND_COMPLETED

    def test_replay(self, tmp_path):
        from strategy_research.core.study.event_store import EventStore, EventType

        store = EventStore(db_path=tmp_path / "events.db")

        store.append(EventType.STUDY_CREATED, "study-1")
        store.append(EventType.STUDY_STARTED, "study-1")
        store.append(EventType.ROUND_COMPLETED, "study-1", {"round": 1})
        store.append(EventType.ROUND_COMPLETED, "study-1", {"round": 2})

        # Replay from seq 1
        events = store.replay("study-1", from_seq=1)
        assert len(events) == 3

    def test_rebuild_state(self, tmp_path):
        from strategy_research.core.study.event_store import EventStore, EventType

        store = EventStore(db_path=tmp_path / "events.db")

        store.append(EventType.STUDY_CREATED, "study-1", {"name": "test"})
        store.append(EventType.STUDY_STARTED, "study-1")
        store.append(EventType.ROUND_COMPLETED, "study-1", {"round": 1, "metrics": {"calmar": 0.5}})
        store.append(EventType.ROUND_KEPT, "study-1", {"run_dir": "rounds/round_0001/run_0001"})

        state = store.rebuild_state("study-1")
        assert state["study_id"] == "study-1"
        assert state["status"] == "running"
        assert state["last_completed_round"] == 1
        assert state["last_keep_run_dir"] == "rounds/round_0001/run_0001"

    def test_snapshot(self, tmp_path):
        from strategy_research.core.study.event_store import EventStore, EventType

        store = EventStore(db_path=tmp_path / "events.db")

        store.append(EventType.STUDY_CREATED, "study-1")
        store.append(EventType.ROUND_COMPLETED, "study-1", {"round": 1})

        # Save snapshot
        store.save_snapshot("study-1", {"last_round": 1, "_seq": 1})

        # Load snapshot
        snapshot = store.get_latest_snapshot("study-1")
        assert snapshot["last_round"] == 1

    def test_listener(self, tmp_path):
        from strategy_research.core.study.event_store import EventStore, EventType

        store = EventStore()
        received_events = []

        def listener(event):
            received_events.append(event)

        store.add_listener(listener)
        store.append(EventType.STUDY_CREATED, "study-1")

        assert len(received_events) == 1
        assert received_events[0].event_type == EventType.STUDY_CREATED

        store.remove_listener(listener)


# ── Activity Tests ─────────────────────────────────────────────────


class TestActivity:
    @pytest.mark.asyncio
    async def test_activity_executor(self, tmp_path):
        from strategy_research.core.study.activity import ActivityExecutor, ActivityStatus
        from strategy_research.core.study.event_store import EventStore

        event_store = EventStore()
        executor = ActivityExecutor(event_store=event_store)

        # Execute file write activity
        result = await executor.execute_activity(
            "write_file",
            "study-1",
            path=str(tmp_path / "test.txt"),
            content="hello world",
        )

        assert result.status == ActivityStatus.COMPLETED
        assert result.result is True
        assert (tmp_path / "test.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_workflow_engine(self):
        from strategy_research.core.study.activity import WorkflowEngine

        engine = WorkflowEngine()

        # Execute activity through workflow engine
        result = await engine.run_activity(
            "write_file",
            "study-1",
            path="/tmp/test_wf.txt",
            content="workflow test",
        )

        assert result is True


# ── Signal/Timer Tests ─────────────────────────────────────────────


class TestSignalTimer:
    @pytest.mark.asyncio
    async def test_signal_send(self):
        from strategy_research.core.study.signals import SignalManager, SignalType

        manager = SignalManager()
        await manager.start()

        signal = await manager.send_signal(
            SignalType.PAUSE,
            "study-1",
            data={"reason": "test"},
        )

        assert signal.signal_type == SignalType.PAUSE
        assert signal.study_id == "study-1"

        await manager.stop()

    @pytest.mark.asyncio
    async def test_timer_creation(self):
        from strategy_research.core.study.signals import SignalManager

        manager = SignalManager()
        await manager.start()

        timer = manager.create_timer(
            "study-1",
            delay_seconds=0.1,
            callback_name="test_callback",
        )

        assert timer.delay_seconds == 0.1
        assert timer.status.value == "pending"

        # Wait for timer to fire
        await asyncio.sleep(0.2)

        await manager.stop()


# ── Checkpoint Tests ───────────────────────────────────────────────


class TestCheckpoint:
    def test_json_checkpoint(self, tmp_path):
        from strategy_research.core.study.checkpoint import (
            CheckpointManager, CheckpointConfig, CheckpointTrigger,
        )

        config = CheckpointConfig(
            location=str(tmp_path / "checkpoints"),
            backend="json",
        )
        manager = CheckpointManager(config)

        # Save checkpoint
        checkpoint = manager.save_checkpoint(
            "study-1",
            {"round": 5, "metrics": {"calmar": 0.6}},
        )

        assert checkpoint.study_id == "study-1"
        assert checkpoint.state["round"] == 5

        # Load checkpoint
        loaded = manager.load_latest("study-1")
        assert loaded is not None
        assert loaded.state["round"] == 5

    def test_sqlite_checkpoint(self, tmp_path):
        from strategy_research.core.study.checkpoint import (
            CheckpointManager, CheckpointConfig,
        )

        config = CheckpointConfig(
            location=str(tmp_path / "checkpoints.db"),
            backend="sqlite",
        )
        manager = CheckpointManager(config)

        # Save multiple checkpoints
        for i in range(5):
            manager.save_checkpoint(
                "study-1",
                {"round": i},
            )

        # Load all
        all_cps = manager.load_all("study-1")
        assert len(all_cps) == 5

        # Load latest
        latest = manager.load_latest("study-1")
        assert latest.state["round"] == 4

    def test_checkpoint_cleanup(self, tmp_path):
        from strategy_research.core.study.checkpoint import (
            CheckpointManager, CheckpointConfig,
        )

        config = CheckpointConfig(
            location=str(tmp_path / "checkpoints"),
            backend="json",
            max_checkpoints=3,
        )
        manager = CheckpointManager(config)

        # Save 5 checkpoints
        for i in range(5):
            manager.save_checkpoint("study-1", {"round": i})

        # Should only keep 3
        all_cps = manager.load_all("study-1")
        assert len(all_cps) == 3


# ── Streaming Tests ────────────────────────────────────────────────


class TestStreaming:
    @pytest.mark.asyncio
    async def test_stream_emitter(self):
        from strategy_research.core.study.streaming import (
            StreamingEmitter, StreamEventType,
        )

        emitter = StreamingEmitter()
        stream = emitter.get_stream("study-1")

        await stream.start()

        # Emit events
        await emitter.emit_study_started("study-1")
        await emitter.emit_round_started("study-1", 1)
        await emitter.emit_round_completed("study-1", 1, metrics={"calmar": 0.5})
        await emitter.emit_study_completed("study-1")

        # Collect events
        events = []
        async for event in stream.iter_events(timeout=0.1):
            events.append(event)

        assert len(events) >= 3
        assert events[0].event_type == StreamEventType.STUDY_STARTED

    @pytest.mark.asyncio
    async def test_stream_tokens(self):
        from strategy_research.core.study.streaming import StreamingEmitter

        emitter = StreamingEmitter()
        stream = emitter.get_stream("study-1")

        await stream.start()

        # Emit tokens
        await emitter.emit_token("study-1", "Hello")
        await emitter.emit_token("study-1", " world")
        await emitter.emit_done("study-1")

        # Collect tokens
        tokens = []
        async for token in stream.iter_tokens():
            tokens.append(token)

        assert tokens == ["Hello", " world"]


# ── DAG Tests ──────────────────────────────────────────────────────


class TestDAG:
    def test_dag_definition(self):
        from strategy_research.core.study.dag import (
            DAGDefinition, TaskDefinition, TaskType,
        )

        dag = DAGDefinition(
            dag_id="test_dag",
            name="Test DAG",
            tasks=[
                TaskDefinition(
                    task_id="task1",
                    task_type=TaskType.AGENT,
                    name="researcher",
                ),
                TaskDefinition(
                    task_id="task2",
                    task_type=TaskType.AGENT,
                    name="analyst",
                    dependencies=["task1"],
                ),
            ],
        )

        assert dag.dag_id == "test_dag"
        assert len(dag.tasks) == 2
        assert dag.tasks[1].dependencies == ["task1"]

    def test_research_dag(self):
        from strategy_research.core.study.dag import create_research_dag

        dag = create_research_dag()
        assert dag.dag_id == "research_pipeline"
        assert len(dag.tasks) == 8

        # Check dependencies
        researcher = dag.tasks[0]
        assert researcher.dependencies == []

        data_quality = dag.tasks[1]
        assert data_quality.dependencies == ["agent_researcher"]

    def test_parallel_research_dag(self):
        from strategy_research.core.study.dag import create_parallel_research_dag

        dag = create_parallel_research_dag()
        assert dag.dag_id == "parallel_research"

        # Check parallel groups
        data_quality = next(t for t in dag.tasks if t.task_id == "agent_data_quality")
        factor_analyst = next(t for t in dag.tasks if t.task_id == "agent_factor_analyst")

        # Both depend only on researcher
        assert data_quality.dependencies == ["agent_researcher"]
        assert factor_analyst.dependencies == ["agent_researcher"]

    @pytest.mark.asyncio
    async def test_dag_scheduler(self):
        from strategy_research.core.study.dag import (
            DAGDefinition, DAGScheduler, TaskDefinition, TaskType, TaskStatus,
        )

        dag = DAGDefinition(
            dag_id="test_dag",
            name="Test DAG",
            tasks=[
                TaskDefinition(
                    task_id="task1",
                    task_type=TaskType.TRANSFORM,
                    name="transform1",
                    config={"transform": lambda ctx: {"result": "done"}},
                ),
                TaskDefinition(
                    task_id="task2",
                    task_type=TaskType.TRANSFORM,
                    name="transform2",
                    config={"transform": lambda ctx: {"result": "done2"}},
                    dependencies=["task1"],
                ),
            ],
        )

        scheduler = DAGScheduler(dag)
        results = await scheduler.execute({"study_id": "study-1"})

        assert results["task1"].status == TaskStatus.COMPLETED
        assert results["task2"].status == TaskStatus.COMPLETED

    def test_execution_order(self):
        from strategy_research.core.study.dag import (
            DAGDefinition, DAGScheduler, TaskDefinition, TaskType,
        )

        dag = DAGDefinition(
            dag_id="test_dag",
            name="Test DAG",
            tasks=[
                TaskDefinition(task_id="c", task_type=TaskType.TRANSFORM, name="c", dependencies=["b"]),
                TaskDefinition(task_id="a", task_type=TaskType.TRANSFORM, name="a"),
                TaskDefinition(task_id="b", task_type=TaskType.TRANSFORM, name="b", dependencies=["a"]),
            ],
        )

        scheduler = DAGScheduler(dag)
        order = scheduler.get_execution_order()

        # a must come before b, b before c
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")


# ── Integration Tests ──────────────────────────────────────────────


class TestIntegration:
    def test_event_store_with_checkpoint(self, tmp_path):
        """Test event store integration with checkpoint manager."""
        from strategy_research.core.study.event_store import EventStore, EventType
        from strategy_research.core.study.checkpoint import (
            CheckpointManager, CheckpointConfig,
        )

        # Create event store
        event_store = EventStore(db_path=tmp_path / "events.db")

        # Create checkpoint manager
        checkpoint_config = CheckpointConfig(
            location=str(tmp_path / "checkpoints.db"),
            backend="sqlite",
        )
        checkpoint_mgr = CheckpointManager(checkpoint_config)

        # Simulate study execution
        for round_num in range(1, 6):
            # Record events
            event_store.append(
                EventType.ROUND_COMPLETED,
                "study-1",
                data={"round": round_num, "metrics": {"calmar": round_num * 0.1}},
            )

            if round_num % 2 == 0:
                # Save checkpoint every 2 rounds
                state = event_store.rebuild_state("study-1")
                checkpoint_mgr.save_checkpoint("study-1", state)

        # Verify
        events = event_store.query("study-1")
        assert len(events) == 5

        checkpoint = checkpoint_mgr.load_latest("study-1")
        assert checkpoint is not None
        assert checkpoint.state["last_completed_round"] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
