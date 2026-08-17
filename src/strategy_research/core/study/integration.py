"""Integration layer — Connects new infrastructure modules to runner/scheduler.

Provides a clean integration of Event Sourcing, Checkpointing, Streaming,
Signals, and DAG into the existing AutoresearchRunner and StudyScheduler.

Usage:
    from strategy_research.core.study.integration import (
        create_enhanced_runner,
        create_enhanced_scheduler,
        load_dag_from_yaml,
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _dlog(module: str, msg: str, *args) -> None:
    msg_fmt = msg % args if args else msg
    logger.info("[INTEGRATION:%s] %s", module, msg_fmt)
    print(f"[INTEGRATION:{module}] {msg_fmt}", flush=True)  # noqa: T201


# ── YAML DAG Loading ───────────────────────────────────────────────


def load_dag_from_yaml(yaml_path: str | Path) -> Any:
    """Load a DAG configuration from a YAML file.

    The YAML file should define:
        dag_id: string
        name: string
        description: string (optional)
        tasks:
            - task_id: string
              task_type: agent | backtest | evaluation | transform
              name: string
              dependencies: list[string] (optional)
              config: dict (optional)
              retry_count: int (optional)
              timeout_seconds: float (optional)
              priority: int (optional)
        metadata: dict (optional)

    Returns:
        DAGDefinition instance
    """
    from .dag import DAGDefinition

    p = Path(yaml_path)
    if not p.exists():
        raise FileNotFoundError(f"DAG file not found: {yaml_path}")

    import yaml
    with open(p) as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"Empty DAG file: {yaml_path}")

    return DAGDefinition.from_dict(data)


def load_dag_from_json(json_path: str | Path) -> Any:
    """Load a DAG configuration from a JSON file."""
    from .dag import DAGDefinition
    return DAGDefinition.from_json(json_path)


# ── Enhanced Runner ────────────────────────────────────────────────


def create_enhanced_runner(
    study: Any,
    store: Any,
    *,
    control: Any | None = None,
    emitter: Any | None = None,
    goal_store: Any | None = None,
    event_store: Any | None = None,
    checkpoint_config: Any | None = None,
    streaming_emitter: Any | None = None,
    dag_yaml_path: str | Path | None = None,
) -> Any:
    """Create an enhanced AutoresearchRunner with integrated infrastructure.

    Args:
        study: StudyRecord instance
        store: StudyStore instance
        control: ControlToken instance (optional)
        emitter: EventEmitter instance (optional)
        goal_store: GoalStore instance (optional)
        event_store: EventStore instance (optional, creates new if None)
        checkpoint_config: CheckpointConfig instance (optional)
        streaming_emitter: StreamingEmitter instance (optional)
        dag_yaml_path: Path to DAG YAML file (optional, uses default if None)

    Returns:
        Enhanced AutoresearchRunner instance
    """
    from .event_store import EventStore, EventType, get_event_store
    from .checkpoint import CheckpointManager, CheckpointConfig
    from .streaming import StreamingEmitter, StreamEventType
    from .dag import DAGScheduler, DAGDefinition

    # Create or use provided event store
    if event_store is None:
        study_root = Path(study.workspace_path) / "study" / study.study_id
        event_db_path = study_root / "events.db"
        event_store = get_event_store(event_db_path)

    # Create or use provided checkpoint manager
    if checkpoint_config is None:
        study_root = Path(study.workspace_path) / "study" / study.study_id
        checkpoint_config = CheckpointConfig(
            location=str(study_root / "checkpoints.db"),
            backend="sqlite",
        )
    checkpoint_mgr = CheckpointManager(checkpoint_config)

    # Create or use provided streaming emitter
    if streaming_emitter is None:
        streaming_emitter = StreamingEmitter()

    # Load DAG if specified
    dag_scheduler = None
    if dag_yaml_path:
        try:
            dag_def = load_dag_from_yaml(dag_yaml_path)
            dag_scheduler = DAGScheduler(dag_def, event_store=event_store)
        except Exception as exc:
            logger.warning("Failed to load DAG from %s: %s", dag_yaml_path, exc)

    # Create enhanced runner
    from .runner import AutoresearchRunner
    runner = AutoresearchRunner(
        study, store,
        control=control,
        emitter=emitter,
        goal_store=goal_store,
    )

    # Attach infrastructure components
    runner._event_store = event_store
    runner._checkpoint_mgr = checkpoint_mgr
    runner._streaming_emitter = streaming_emitter
    runner._dag_scheduler = dag_scheduler

    # Wrap key methods with event recording
    _patch_runner_with_events(runner, event_store, checkpoint_mgr, streaming_emitter)

    return runner


def _patch_runner_with_events(
    runner: Any,
    event_store: Any,
    checkpoint_mgr: Any,
    streaming_emitter: Any,
) -> None:
    """Patch runner methods to record events and checkpoints."""

    from .event_store import EventType
    from .streaming import StreamEventType

    # Store original methods
    original_run_loop = runner._run_loop
    original_run_one_round = runner._run_one_round

    async def enhanced_run_loop() -> str:
        """Enhanced run loop with event recording."""
        sid = runner._get_study().study_id

        # Record study started
        event_store.append(
            EventType.STUDY_STARTED,
            sid,
            data={"round": runner._get_study().current_round},
        )

        # Stream study started
        await streaming_emitter.emit_study_started(sid)

        # Run original loop
        reason = await original_run_loop()

        # Record study completion
        event_store.append(
            EventType.STUDY_COMPLETED if reason == "targets_met" else EventType.STUDY_ERROR,
            sid,
            data={"reason": reason},
        )

        # Stream study completed
        if reason == "targets_met":
            await streaming_emitter.emit_study_completed(sid)
        else:
            await streaming_emitter.emit_error(sid, reason)

        return reason

    def enhanced_run_one_round(
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Enhanced round execution with event recording."""
        sid = runner._get_study().study_id

        # Record round started
        event_store.append(
            EventType.ROUND_STARTED,
            sid,
            data={"round": round_num},
        )

        # Run original round
        result = original_run_one_round(round_num, previous_summary, directive_text)

        # Record round completed
        event_store.append(
            EventType.ROUND_COMPLETED,
            sid,
            data={
                "round": round_num,
                "metrics": result.get("metrics", {}),
                "verdict": result.get("verdict"),
            },
        )

        # Save checkpoint if configured
        if checkpoint_mgr.should_checkpoint("round_completed"):
            state = {
                "round": round_num,
                "metrics": result.get("metrics", {}),
                "verdict": result.get("verdict"),
            }
            checkpoint_mgr.save_checkpoint(sid, state)

        return result

    # Patch methods
    runner._run_loop = enhanced_run_loop
    runner._run_one_round = enhanced_run_one_round


# ── Enhanced Scheduler ─────────────────────────────────────────────


def create_enhanced_scheduler(
    store: Any,
    *,
    session_service: Any | None = None,
    emitter_factory: Any | None = None,
    signal_manager: Any | None = None,
    checkpoint_config: Any | None = None,
) -> Any:
    """Create an enhanced StudyScheduler with integrated infrastructure.

    Args:
        store: StudyStore instance
        session_service: SessionService instance (optional)
        emitter_factory: Emitter factory function (optional)
        signal_manager: SignalManager instance (optional)
        checkpoint_config: CheckpointConfig instance (optional)

    Returns:
        Enhanced StudyScheduler instance
    """
    from .scheduler import StudyScheduler
    from .signals import SignalManager, get_signal_manager
    from .checkpoint import CheckpointManager, CheckpointConfig

    # Create scheduler
    scheduler = StudyScheduler(
        store,
        session_service=session_service,
        emitter_factory=emitter_factory,
    )

    # Create or use provided signal manager
    if signal_manager is None:
        signal_manager = get_signal_manager(
            scheduler=scheduler,
            study_store=store,
        )

    # Create or use provided checkpoint manager
    if checkpoint_config is None:
        checkpoint_config = CheckpointConfig(
            location=".checkpoints",
            backend="sqlite",
        )
    checkpoint_mgr = CheckpointManager(checkpoint_config)

    # Attach infrastructure components
    scheduler._signal_manager = signal_manager
    scheduler._checkpoint_mgr = checkpoint_mgr

    return scheduler


# ── Convenience: Run with YAML DAG ────────────────────────────────


def run_study_with_dag(
    study: Any,
    store: Any,
    dag_yaml_path: str | Path,
    **kwargs: Any,
) -> Any:
    """Create and run an enhanced runner with a YAML DAG configuration.

    This is a convenience function that:
    1. Loads the DAG from YAML
    2. Creates an enhanced runner with the DAG
    3. Runs the study

    Args:
        study: StudyRecord instance
        store: StudyStore instance
        dag_yaml_path: Path to DAG YAML file
        **kwargs: Additional arguments for create_enhanced_runner

    Returns:
        Shutdown reason string
    """
    runner = create_enhanced_runner(
        study, store,
        dag_yaml_path=dag_yaml_path,
        **kwargs,
    )

    import asyncio
    return asyncio.run(runner.run())
