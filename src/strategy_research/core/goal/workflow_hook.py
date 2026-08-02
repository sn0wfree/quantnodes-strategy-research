"""GoalWorkflowHook — goal-specific hook for SwarmRuntime (P3.2).

Implements ``SwarmHook`` to wire DAG execution to GoalStore:

  - ``on_agent_complete``: auto-collect evidence, save to RunStore
  - ``on_layer_complete``: check criteria coverage
  - ``should_stop``: return True when goal is complete

This hook is the bridge between the generic SwarmRuntime and the
Goal-specific state management.  GoalWorkflowRunner (P3.9) will
delegate to SwarmRuntime + this hook instead of reimplementing DAG
execution.
"""
from __future__ import annotations

import logging
from typing import Any

from ..workflow.types import SwarmHook

logger = logging.getLogger(__name__)


class GoalWorkflowHook:
    """SwarmRuntime hook for goal-specific behavior.

    When plugged into ``SwarmRuntime.execute(hooks=[hook])``, this
    hook automatically:
      1. Collects evidence from agent outputs
      2. Saves to RunStore (if provided)
      3. Checks criteria coverage after each layer
      4. Signals ``should_stop`` when all criteria are covered

    Args:
        session_id: Current session id.
        goal_id: Active goal id.
        evidence_map: Mapping ``agent_id → criterion_idx``.
        store: GoalStore instance.
        run_store: Optional RunStore instance for persistent logging.
        run_id: Current run id (for RunStore lookups).
        completion_strategy: CompletionStrategy to call on completion.
        completion_mode: "auto" | "lite" | "manual".
        workflow_name: Human-readable workflow name for audit notes.
        event_bus: Optional WorkflowEventBus for state-change notifications.
    """

    def __init__(
        self,
        session_id: str,
        goal_id: str,
        evidence_map: dict[str, int],
        store: Any,
        *,
        runner: Any = None,
        run_store: Any = None,
        run_id: str = "",
        completion_strategy: Any = None,
        completion_mode: str = "auto",
        workflow_name: str = "",
        event_bus: Any = None,
    ) -> None:
        self._session_id = session_id
        self._goal_id = goal_id
        self._evidence_map = evidence_map
        self._store = store
        self._runner = runner  # P1.2: reference for should_stop() cancelled check
        self._run_store = run_store
        self._run_id = run_id
        self._completion_strategy = completion_strategy
        self._completion_mode = completion_mode
        self._workflow_name = workflow_name
        self._event_bus = event_bus
        self._completed = False
        self._evidence_count = 0
        self._layer_results: dict[str, Any] = {}  # P1.3: saved/restored on checkpoint

    @property
    def name(self) -> str:
        return "GoalWorkflowHook"

    @property
    def completed(self) -> bool:
        """True if the hook triggered auto-completion."""
        return self._completed

    @property
    def evidence_count(self) -> int:
        """Number of evidence records collected."""
        return self._evidence_count

    # ── SwarmHook callbacks ────────────────────────────────────

    def on_layer_start(
        self,
        layer_idx: int,
        agents: list[str],
        context: dict[str, Any],
    ) -> None:
        logger.info("GoalWorkflowHook: layer %d starting, agents=%s", layer_idx, agents)
        runner_state = getattr(self._runner, "_state", None)
        if runner_state is not None:
            runner_state.current_layer = layer_idx + 1
            for aid in agents:
                runner_state.set_agent_status(aid, "running")
        if self._event_bus:
            self._event_bus.emit("layer_start", layer=layer_idx, agents=agents)

    def on_agent_complete(
        self,
        agent_id: str,
        result: Any,
        context: dict[str, Any],
    ) -> None:
        # Extract output text from AgentResult or dict
        output_text = self._extract_output(result)
        if not output_text:
            return

        runner_state = getattr(self._runner, "_state", None)
        if runner_state is not None:
            result_status = getattr(result, "status", None)
            runner_state.set_agent_status(
                agent_id,
                "skipped" if result_status == "skipped" else "success",
            )

        # Auto-collect evidence
        criterion_idx = self._evidence_map.get(agent_id, -1)
        if criterion_idx >= 0:
            collected = self._collect_evidence(agent_id, output_text, criterion_idx)
            if collected:
                self._evidence_count += 1
                logger.info(
                    "GoalWorkflowHook: collected evidence from %s (total=%d)",
                    agent_id, self._evidence_count,
                )

        # Save to RunStore
        if self._run_store and self._run_id:
            try:
                self._run_store.save_agent_output(
                    self._run_id, agent_id,
                    {"answer": output_text, "agent_id": agent_id},
                )
            except Exception as exc:
                logger.warning("RunStore save failed for %s: %s", agent_id, exc)

        if self._event_bus:
            self._event_bus.emit("agent_complete", agent_id=agent_id)

    def on_layer_complete(
        self,
        layer_idx: int,
        agents: list[str],
        results: dict[str, Any],
    ) -> None:
        if self._event_bus:
            self._event_bus.emit("layer_complete", layer=layer_idx, agents=agents)

        # P1.3: capture real layer_results so checkpoints save agent outputs.
        for aid, res in results.items():
            try:
                self._layer_results[aid] = {"output": self._parse_output(res)}
            except Exception:  # noqa: BLE001
                logger.warning("Failed to capture layer result for %s", aid)

        # Check if all criteria are covered
        if self._check_all_criteria_covered():
            logger.info(
                "GoalWorkflowHook: all criteria covered after layer %d, "
                "triggering auto-complete", layer_idx,
            )
            self._auto_complete()

    @staticmethod
    def _parse_output(result: Any) -> Any:
        """Parse an AgentResult/dict output into a JSON-able object."""
        import json
        raw = None
        if hasattr(result, "output"):
            raw = result.output
        elif isinstance(result, dict):
            raw = result.get("output", result.get("answer", ""))
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
        return raw

    def should_stop(self) -> bool:
        """Return True if the goal has been completed or the runner is cancelled.

        Phase 4 P1.2: Checks ``runner._state.cancelled`` so that
        ``pause(immediate=True)`` actually interrupts DAG execution
        via SwarmRuntime's ``should_stop`` hook.
        """
        # Check runner's cancelled flag (P1.2)
        runner_state = getattr(self._runner, "_state", None)
        if runner_state and getattr(runner_state, "cancelled", False):
            return True
        return self._completed

    # ── Internal helpers ───────────────────────────────────────

    def _extract_output(self, result: Any) -> str:
        """Extract text output from an AgentResult or dict."""
        if result is None:
            return ""
        # AgentResult object
        if hasattr(result, "output"):
            return str(result.output or "")
        # Dict form
        if isinstance(result, dict):
            return str(result.get("answer", result.get("output", "")))
        return str(result)

    def _collect_evidence(
        self,
        agent_id: str,
        output_text: str,
        criterion_idx: int,
    ) -> bool:
        """Append agent output as goal evidence. Returns True on success."""
        from .models import EvidenceInput

        if len(output_text.strip()) < 10:
            return False

        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return False

        criteria = snapshot.get("criteria", [])
        if criterion_idx < 0 or criterion_idx >= len(criteria):
            return False

        criterion_id = criteria[criterion_idx].get("criterion_id")
        if not criterion_id:
            return False

        try:
            self._store.append_evidence(
                session_id=self._session_id,
                goal_id=self._goal_id,
                expected_goal_id=self._goal_id,
                evidence=EvidenceInput(
                    criterion_id=criterion_id,
                    text=output_text[:2000],
                    source_provider="workflow",
                    source_type=agent_id,
                ),
            )
            return True
        except Exception as exc:
            logger.warning("Evidence collection failed for %s: %s", agent_id, exc)
            return False

    def _check_all_criteria_covered(self) -> bool:
        """Check if all required criteria have at least one evidence."""
        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return False

        criteria = snapshot.get("criteria", [])
        evidence = snapshot.get("evidence", [])

        evidence_by_criterion: dict[str, list] = {}
        for ev in evidence:
            cid = ev.get("criterion_id")
            if cid:
                evidence_by_criterion.setdefault(cid, []).append(ev)

        for criterion in criteria:
            if not criterion.get("required", True):
                continue
            cid = criterion.get("criterion_id", "")
            if not evidence_by_criterion.get(cid):
                return False
        return True

    def _auto_complete(self) -> None:
        """Trigger goal completion via the configured strategy."""
        if self._completed:
            return

        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return

        from .completion_strategy import CompletionStrategyFactory

        strategy = self._completion_strategy or CompletionStrategyFactory.get(
            self._completion_mode,
        )

        try:
            import asyncio
            # If we're already in an async context, schedule; otherwise call sync
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(strategy.complete(
                    self._store, self._session_id, self._goal_id,
                    snapshot.get("criteria", []),
                    snapshot.get("evidence", []),
                    self._workflow_name,
                ))
            except RuntimeError:
                # No running loop — run synchronously
                asyncio.run(strategy.complete(
                    self._store, self._session_id, self._goal_id,
                    snapshot.get("criteria", []),
                    snapshot.get("evidence", []),
                    self._workflow_name,
                ))

            self._completed = True
            logger.info("Goal %s auto-completed via %s", self._goal_id, type(strategy).__name__)
            if self._event_bus:
                self._event_bus.emit("workflow_completed", goal_id=self._goal_id)

        except Exception as exc:
            logger.error("Auto-completion failed: %s", exc)
            if self._event_bus:
                self._event_bus.emit("workflow_failed", error=str(exc))


__all__ = ["GoalWorkflowHook"]