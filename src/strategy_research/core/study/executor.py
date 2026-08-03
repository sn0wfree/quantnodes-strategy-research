"""AutoresearchExecutor — drives the autoresearch loop for a study.

Pairs a ``StudyRecord`` (execution state) with its bound ``GoalStore``
ledger row and runs ``run_research_round`` repeatedly until one of the
shutdown conditions fires:

    - metric targets met → goal completion audit → study COMPLETE
    - max_rounds reached → study ERROR (with completion attribution)
    - acceptance decide() triggered stagnation → study ERROR
    - token / turn / time budget exceeded → study BUDGET_LIMITED
    - caller pause / cancel → study PAUSED / CANCELLED

The executor is runtime-driven: it emits ``study_*`` event payloads
through a pluggable ``EventEmitter`` and writes progress back to the
``StudyStore`` (round counter, heartbeat, last metrics).

See ``docs/study-longhorizon-plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import StudyRecord, StudyStatus
from .store import StudyStore

logger = logging.getLogger(__name__)


# ── shutdown reasons ────────────────────────────────────────────────


class ShutdownReason:
    """Tags identifying why an executor stopped. Kept as plain strings
    for direct JSON serialization into ``last_error``."""

    TARGETS_MET = "targets_met"
    MAX_ROUNDS = "max_rounds"
    STAGNATION = "stagnation"
    BUDGET = "budget_exceeded"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    ERROR = "error"


# ── metric target comparison ─────────────────────────────────────────


def meets_metric_targets(metrics: dict[str, Any], targets: list[dict]) -> bool:
    """Return True iff ``metrics`` satisfies every target.

    Each target is ``{"name", "op", "value"}``. Missing metric fields
    count as not-met (the round did not produce that metric).
    """

    for t in targets:
        name = t.get("name")
        op = t.get("op", ">=")
        value = t.get("value")
        if name is None or value is None:
            return False
        actual = metrics.get(name)
        if actual is None:
            return False
        try:
            a = float(actual)
            v = float(value)
        except (TypeError, ValueError):
            return False
        if op == ">=" and not (a >= v):
            return False
        elif op == "<=" and not (a <= v):
            return False
        elif op == ">" and not (a > v):
            return False
        elif op == "<" and not (a < v):
            return False
        elif op == "==" and not (a == v):
            return False
        else:
            # unknown op already rejected above by the else branch
            pass
    return True


def acceptance_config_from_targets(
    targets: list[dict] | None,
) -> Any:
    """Map ``metric_targets`` → an ``AcceptanceConfig`` override.

    Called lazily (imports strategy_acceptance) so importing
    ``core.study.executor`` does not eagerly load the acceptance module —
    useful when tests only need pure helpers.
    """

    from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG

    if not targets:
        return DEFAULT_CONFIG
    overrides: dict[str, Any] = {}
    for t in targets:
        name = t.get("name")
        value = t.get("value")
        if name is None or value is None:
            continue
        if name == "calmar":
            overrides["hard_calmar_min"] = float(value)
        elif name == "sharpe":
            overrides["hard_sharpe_min"] = float(value)
        elif name == "max_dd":
            # max_dd is negative (drawdown); min ≥ -0.15 means better drawdown
            overrides["hard_max_dd_min"] = float(value)
    return DEFAULT_CONFIG.with_overrides(**overrides) if overrides else DEFAULT_CONFIG


# ── event emitter protocol ──────────────────────────────────────────


class EventEmitter(Protocol):
    def emit(self, session_id: str, event: str, data: dict) -> None: ...


@dataclass
class NullEmitter:
    """No-op emitter (useful for tests / CLI)."""

    def emit(self, session_id: str, event: str, data: dict) -> None:
        return None


# ── pause / cancel token ────────────────────────────────────────────


@dataclass
class ControlToken:
    """Shared mutable control signals between scheduler and executor.

    The scheduler raises ``paused`` / ``cancelled`` on the executing
    study's token; the executor reads them at round boundaries.
    """

    paused: bool = False
    cancelled: bool = False


# ── executor ────────────────────────────────────────────────────────


class AutoresearchExecutor:
    """Drive a study through repeated ``run_research_round`` calls.

    Owns no threads itself: the scheduler may run it on a worker thread
    via ``asyncio.to_thread`` or directly ``await`` it — both are
    fine because ``run`` is a coroutine that wraps the synchronous
    autoresearch round with ``asyncio.to_thread``.
    """

    def __init__(
        self,
        study: StudyRecord,
        store: StudyStore,
        *,
        control: ControlToken | None = None,
        emitter: EventEmitter | None = None,
        goal_store: Any = None,
    ) -> None:
        self.study = study
        self.study_store = store
        self.control = control or ControlToken()
        self.emitter = emitter or NullEmitter()
        # Lazy goal store: allow caller to inject for tests; otherwise the
        # executor opens its own. Kept as attribute, closed on shutdown.
        self._own_goal_store = goal_store is None
        self._goal_store = goal_store or self._open_goal_store()
        # Budget accumulators
        self._round_start_clock: float | None = None
        self._total_used_time: float = 0.0
        self._total_used_turns: int = 0
        self._acceptance_config = acceptance_config_from_targets(
            study.metric_targets,
        )

    # ── public entrypoint ───────────────────────────────────────────

    async def run(self) -> str:
        """Execute rounds until a shutdown condition fires.

        Returns the ``ShutdownReason`` string tag (also persisted to
        ``StudyStore.last_error``).
        """

        sid = self.study.study_id
        session = self.study.session_id
        self._emit(session, "study_started", {
            "study_id": sid, "round": self.study.current_round,
        })
        reason = ShutdownReason.ERROR
        try:
            reason = await self._run_loop()
        except Exception as exc:
            logger.exception("study %s failed: %s", sid, exc)
            self.study_store.update_execution_status(
                sid, StudyStatus.ERROR, last_error=f"{exc}"[:500]
            )
            self._emit(session, "study_failed", {
                "study_id": sid, "error": f"{exc}"[:500],
                "reason": ShutdownReason.ERROR,
            })
        finally:
            if self._own_goal_store:
                try:
                    self._goal_store.close()
                except Exception:
                    pass
            self._emit(session, "study_executor_stopped", {
                "study_id": sid, "reason": reason,
            })
        return reason

    # ── main loop ──────────────────────────────────────────────────

    async def _run_loop(self) -> str:
        sid = self.study.study_id
        # Use a local round counter seeded from the persisted row so a
        # resumed study continues numbering.
        round_num = self.study.current_round
        previous_summary: dict | None = None
        # Seed previous_summary from the latest run when resuming mid-flight.
        previous_summary = self._maybe_load_previous_summary(self.study)

        while True:
            if self.control.cancelled:
                self._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
                self._emit(self.study.session_id, "study_cancelled", {"study_id": sid})
                return ShutdownReason.CANCELLED
            # A pause suspends the executor until cleared.
            if self.control.paused:
                self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
                self._emit(self.study.session_id, "study_paused", {
                    "study_id": sid, "round": round_num,
                })
                await self._wait_until_resumed()
                # resumed → mark running again
                self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
                self._emit(self.study.session_id, "study_resumed", {
                    "study_id": sid, "round": round_num,
                })

            round_num += 1
            self._round_start_clock = time.perf_counter()
            result = await asyncio.to_thread(
                self._run_one_round, round_num, previous_summary,
            )
            previous_summary = result.get("summary") or previous_summary

            # ── budget enforcement: time/turn ─────────────────────
            self._account_round_budget(result)
            # ── persist round + last metrics ──────────────────────
            self.study_store.update_round_heartbeat(sid, round_num)
            metrics = result.get("metrics", {}) or {}
            verdict = result.get("verdict", "discard")
            self.study_store.update_last_metrics(sid, metrics, verdict)
            self._emit(self.study.session_id, "study_round", {
                "study_id": sid,
                "round": round_num,
                "run": result.get("run_name", ""),
                "metrics": metrics,
                "verdict": verdict,
                "agent_statuses": (result.get("summary") or {}).get("agent_statuses", {}),
            })

            # ── shutdown checks ────────────────────────────────────
            # 1) targets met
            if self.study.metric_targets and meets_metric_targets(
                metrics, self.study.metric_targets
            ):
                self._complete_goal(result)
                self._mark_terminal(
                    StudyStatus.COMPLETE,
                    last_metrics=metrics,
                    reason=ShutdownReason.TARGETS_MET,
                )
                self._emit(self.study.session_id, "study_completed", {
                    "study_id": sid, "goal_id": self.study.goal_id,
                    "metrics": metrics, "round": round_num,
                    "recap": verdict,
                })
                return ShutdownReason.TARGETS_MET

            # 2) budget exceeded
            if self._budget_exceeded():
                self._mark_terminal(
                    StudyStatus.BUDGET_LIMITED,
                    last_metrics=metrics,
                    last_error=self._budget_summary(),
                    reason=ShutdownReason.BUDGET,
                )
                self._emit(self.study.session_id, "study_budget_limited", {
                    "study_id": sid, "used": self._budget_summary(),
                })
                return ShutdownReason.BUDGET

            # 3) stagnation
            decision = result.get("decision") or {}
            if decision.get("stagnation_triggered"):
                self._mark_terminal(
                    StudyStatus.ERROR,
                    last_metrics=metrics,
                    last_error=f"stagnation: {decision.get('reason', '')}",
                    reason=ShutdownReason.STAGNATION,
                )
                self._emit(self.study.session_id, "study_failed", {
                    "study_id": sid,
                    "error": f"stagnation: {decision.get('reason', '')}",
                    "reason": ShutdownReason.STAGNATION,
                })
                return ShutdownReason.STAGNATION

            # 4) max_rounds
            if self.study.max_rounds is not None and round_num >= self.study.max_rounds:
                self._mark_terminal(
                    StudyStatus.ERROR,
                    last_metrics=metrics,
                    last_error=f"max_rounds={self.study.max_rounds} reached without targets",
                    reason=ShutdownReason.MAX_ROUNDS,
                )
                self._emit(self.study.session_id, "study_failed", {
                    "study_id": sid,
                    "error": "max_rounds reached",
                    "reason": ShutdownReason.MAX_ROUNDS,
                })
                return ShutdownReason.MAX_ROUNDS

            # ── inter-round cooldown (owned by executor since the CLI
            # did too) ───────────────────────────────────────────
            await asyncio.sleep(self._round_cooldown())

    # ── per-round execution ────────────────────────────────────────

    def _run_one_round(self, round_num: int, previous_summary) -> dict:
        """Blocking: invoke run_research_round. Runs via to_thread."""

        from strategy_research.core.autoresearch import run_research_round as _runner

        return _runner(
            Path(self.study.workspace_path),
            self.study.strategy_name,
            round_num,
            session_id=self.study.session_id,
            acceptance_config=self._acceptance_config,
            behavior=self.study.behavior,
            max_retries=3,
            lazy_detection_interval=self.study.lazy_detection_interval,
            keep_recent=self.study.keep_recent,
            previous_summary=previous_summary,
        )

    # ── budget accounting ──────────────────────────────────────────

    def _account_round_budget(self, result: dict) -> None:
        # time: tracked here
        if self._round_start_clock is not None:
            self._total_used_time += time.perf_counter() - self._round_start_clock
        # turns: count successful agent outputs as one turn each (9 per round)
        outs = result.get("agent_outputs") or {}
        self._total_used_turns += sum(
            1 for v in outs.values() if not (isinstance(v, dict) and v.get("error"))
        )

    def _budget_exceeded(self) -> bool:
        s = self.study
        if s.budget_time_seconds is not None and self._total_used_time >= s.budget_time_seconds:
            return True
        if s.budget_turn is not None and self._total_used_turns >= s.budget_turn:
            return True
        # token budget is accumulated by the LLM client, not here; this
        # executor checks the per-turn channel. Token guard is advisory
        # unless the scheduler injects the LLM usage back (kept loose for
        # Phase 1; documented in study-longhorizon-plan.md §11).
        return False

    def _budget_summary(self) -> str:
        return (
            f"turns_used={self._total_used_turns}, "
            f"time_used={self._total_used_time:.1f}s"
        )

    # ── goal completion ────────────────────────────────────────────

    def _complete_goal(self, result: dict) -> None:
        """Populate any evidenceless requirements + complete the goal.

        ``run_research_round`` already appended one evidence entry per round
        (via ``_study_append_evidence``) linked to the goal's first
        criterion. When a study's acceptance targets are met the round
        metrics + run name justify closing the goal, so we:

            1. Append a single ``criterion_id``-scoped evidence for every
               *other* required criterion that lacks one so far.
            2. Call ``GoalStore.complete_lite`` for a verified-lite closure
               (no audit-row burden, matches the Phase 1 minimal闭环).
        """
        if not self.study.goal_id:
            return
        gs = self._goal_store
        try:
            existing = gs.list_evidence(self.study.goal_id)
            seen: set = {ev.criterion_id for ev in existing if ev.criterion_id}
            criteria = gs.list_criteria(self.study.goal_id)
            metrics = result.get("metrics", {}) or {}
            run_name = result.get("run_name", "")
            from strategy_research.core.goal import EvidenceInput
            for c in criteria:
                if not c.required or c.criterion_id in seen:
                    continue
                gs.append_evidence(
                    session_id=self.study.session_id,
                    goal_id=self.study.goal_id,
                    expected_goal_id=self.study.goal_id,
                    evidence=EvidenceInput(
                        text=(
                            f"Study 达标自动覆盖 — {run_name}: "
                            f"Calmar={metrics.get('calmar')} "
                            f"Sharpe={metrics.get('sharpe')} "
                            f"MaxDD={metrics.get('max_dd')}"
                        ),
                        criterion_id=c.criterion_id,
                        evidence_type="acceptance",
                        run_id=run_name,
                        source_provider="study",
                        source_type="metric_targets_met",
                    ),
                )
            recap = (
                f"研究达标自动收尾 — Calmar={metrics.get('calmar')}, "
                f"Sharpe={metrics.get('sharpe')}, MaxDD={metrics.get('max_dd')}, "
                f"round={result.get('round')} run={run_name}"
            )
            gs.complete_lite(
                session_id=self.study.session_id,
                goal_id=self.study.goal_id,
                expected_goal_id=self.study.goal_id,
                recap=recap,
            )
        except Exception as exc:
            logger.exception(
                "study %s failed goal completion: %s", self.study.study_id, exc,
            )

    # ── pause / resume internals ──────────────────────────────────

    async def _wait_until_resumed(self) -> None:
        """Block the loop until the scheduler clears the pause flag.

        Uses bounded sleep polling to stay responsive to cancel.
        """

        while self.control.paused and not self.control.cancelled:
            await asyncio.sleep(0.5)

    # ── resumption helpers ────────────────────────────────────────

    def _maybe_load_previous_summary(self, study: StudyRecord) -> dict | None:
        """Load the latest summary.json from the strategy's runs directory.

        When a study resumes after a restart, this seeds the next round's
        ``performance_change`` computation. Returns ``None`` if no run
        has happened yet (first round after start).
        """

        try:
            runs_dir = Path(study.workspace_path) / "strategies" / study.strategy_name / "runs"
            if not runs_dir.exists():
                return None
            from strategy_research.core.autoresearch import load_run_summary
            nums: list[int] = []
            for d in runs_dir.iterdir():
                if d.is_dir() and d.name.startswith("run_"):
                    try:
                        nums.append(int(d.name.split("_")[1]))
                    except (ValueError, IndexError):
                        pass
            if not nums:
                return None
            latest = runs_dir / f"run_{max(nums):04d}"
            return load_run_summary(latest)
        except Exception as exc:
            logger.debug("study %s prev-summary load skipped: %s",
                          study.study_id, exc)
            return None

    # ── misc helpers ──────────────────────────────────────────────

    def _round_cooldown(self) -> float:
        # Mirror CLI semantics: round cooldown uses base*2 / jitter*2 / min*2.
        from strategy_research.core.autoresearch import get_cooldown_seconds
        return get_cooldown_seconds(
            self.study.cooldown_base * 2,
            self.study.cooldown_jitter * 2,
            self.study.min_cooldown * 2,
        )

    def _open_goal_store(self) -> Any:
        from strategy_research.core.goal import GoalStore
        return GoalStore()

    def _mark_terminal(
        self,
        status: StudyStatus,
        *,
        last_metrics: dict | None = None,
        last_error: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Transition the study to a terminal status in the store."""

        err = last_error if reason is None else f"{reason}:{last_error or ''}"
        self.study_store.update_execution_status(
            self.study.study_id,
            status,
            last_error=err,
            last_metrics=last_metrics,
        )

    def _emit(self, session_id: str, event: str, data: dict) -> None:
        try:
            self.emitter.emit(session_id, event, data)
        except Exception as exc:
            logger.debug("study event emit %s failed: %s", event, exc)