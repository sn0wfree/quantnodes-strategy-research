"""AutoresearchRunner — AEGIS-powered study executor.

Upgraded version of ``AutoresearchExecutor`` with built-in AEGIS mechanisms:
Novelty Gate, Attribution, Lever Scoreboard, Regression Gate, and Early-stop.

Uses the phase-split ``run_researcher_phase`` / ``run_execution_phase`` /
``run_evaluation_phase`` from ``autoresearch.py`` so AEGIS hooks can be
injected between phases.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..observability import new_trace_id
from .models import StudyRecord, StudyStatus
from .store import StudyStore
from .runner_context import RunnerContext

logger = logging.getLogger(__name__)

# v2 review-cycle tuning (design §10/§11)
SR_STUDY_MAX_DEVIATION = 3          # consecutive high deviations → stop
SR_STUDY_COLLECT_INTERVAL = 5       # force info collection every K rounds
SR_STUDY_MAX_DISCARD = 5            # consecutive discards → stagnation stop

# Agent resilience (Step B of round/retry/loop fix)
SR_AGENT_MAX_ITER = 50              # high-iteration default for complex agents
SR_AGENT_NO_PROGRESS_WINDOW = 5     # tolerate 5 identical tool calls before approval gate
SR_AGENT_MAX_PARSE_RETRIES = 2      # retries per round on parse_failed
SR_AGENT_PARSE_BACKOFF_BASE = 5.0   # base seconds for parse_failed backoff
SR_AGENT_PARSE_BACKOFF_MAX = 30.0   # cap on backoff wait

# Agent names whose parse_failed output triggers round-level retry
_PARSE_FAILED_AGENT_KEYS: tuple[str, ...] = (
    "data_quality_output",
    "factor_analyst_output",
    "strategist_output",
    "portfolio_construction_output",
)


def _parse_failed_agents(exec_result: dict) -> list[str]:
    """Return the names of agents that returned parse_failed for this round."""
    failed: list[str] = []
    for key in _PARSE_FAILED_AGENT_KEYS:
        out = exec_result.get(key)
        if isinstance(out, dict) and out.get("error") == "parse_failed":
            # key like "data_quality_output" → "data_quality"
            failed.append(key[: -len("_output")])
    return failed


def _dlog(module: str, msg: str, *args) -> None:
    msg_fmt = msg % args if args else msg
    logger.info("[RUNNER:%s] %s", module, msg_fmt)
    print(f"[RUNNER:{module}] {msg_fmt}", flush=True)  # noqa: T201


# ── shutdown reasons ────────────────────────────────────────────────


class ShutdownReason:
    TARGETS_MET = "targets_met"
    MAX_ROUNDS = "max_rounds"
    STAGNATION = "stagnation"
    BUDGET = "budget_exceeded"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    ERROR = "error"
    EARLY_STOPPED = "early_stopped"
    NOVELTY_REJECTED = "novelty_rejected"
    # v2 review cycle (design §10.2/§10.3)
    REPEATED_DEVIATION = "repeated_deviation"
    REVIEW_FAILED = "review_failed"
    DISCARD_STREAK = "stagnation_discard_streak"
    # v2 monitor (design §15)
    NEEDS_REFRESH = "needs_refresh"


# ── metric target comparison (reused from executor.py) ──────────────
from .metric_targets import meets_metric_targets, metric_pass_set as _metric_pass_set, acceptance_config_from_targets  # noqa: F401


# ── event emitter protocol ──────────────────────────────────────────


class EventEmitter(Protocol):
    def emit(self, session_id: str, event: str, data: dict) -> None: ...


@dataclass
class NullEmitter:
    def emit(self, session_id: str, event: str, data: dict) -> None:
        return None


# ── control token ───────────────────────────────────────────────────


@dataclass
class ControlToken:
    paused: bool = False
    cancelled: bool = False


# ── AutoresearchRunner ─────────────────────────────────────────────


class AutoresearchRunner:
    """AEGIS-powered study executor with round-loop + AEGIS hooks.

    Replaces AutoresearchExecutor for studies using executor_type='autoresearch'.
    The round loop calls phase-split functions from autoresearch.py, with AEGIS
    mechanisms injected between phases.
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
        self.study_id = study.study_id
        self.study_store = store
        self.control = control or ControlToken()
        self.emitter = emitter or NullEmitter()
        self._own_goal_store = goal_store is None
        self._goal_store = goal_store or self._open_goal_store()
        # AEGIS state
        self._prev_passed: set[str] = set()
        self._best_score: float = 0.0
        self._idle_rounds: int = 0
        # Agent resilience: ExplorerStrategy (max_iter=50, no_progress_window=5)
        # replaces the hardcoded defaults of 10/3 to give complex agents
        # (strategist, factor_analyst) more headroom before the no_progress
        # approval gate fires.
        try:
            from ..agent.strategy.explorer import ExplorerStrategyFactory
            self._loop_strategy = ExplorerStrategyFactory.create()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ExplorerStrategy unavailable, falling back to default: %s", exc)
            self._loop_strategy = None
        # Budget accumulators
        self._round_start_clock: float | None = None
        self._total_used_time: float = 0.0
        self._total_used_turns: int = 0
        # Study-row cache for live reads: DB is the source of truth for
        # mutable fields (current_round, last_metrics, last_verdict,
        # execution_status), but re-reading on every access is wasteful.
        # 5s TTL keeps monitor-loop snapshots fresh without hammering
        # SQLite. Writers (cancel/pause/cancel_study) call
        # ``invalidate_study_cache()`` after ``update_execution_status``.
        self._study_cache: StudyRecord | None = None
        self._study_cache_ts: float = 0.0
        # v4: study-scoped trace id — stable across rounds so every SSE
        # event + log line for this study correlates to one research line.
        self._trace_id = new_trace_id()

    def _get_study(self, *, force: bool = False) -> StudyRecord:
        """Return current ``StudyRecord`` from DB, cached for ~5s.

        The constructor's ``study`` arg is treated as the initial value;
        subsequent reads pick up DB mutations (current_round,
        last_metrics, execution_status, etc.) without requiring the
        caller to manually re-fetch.
        """
        now = time.monotonic()
        if not force and self._study_cache is not None and (now - self._study_cache_ts) < 5.0:
            return self._study_cache
        loaded = self.study_store.get_study(self.study_id)
        if loaded is None:
            return self.study  # study row vanished; fall back to last known
        self._study_cache = loaded
        self._study_cache_ts = now
        return loaded

    def invalidate_study_cache(self) -> None:
        self._study_cache = None
        self._study_cache_ts = 0.0

    def _current_db_status(self) -> StudyStatus | None:
        """Bypass the 5s study cache to read the freshest execution_status."""
        try:
            fresh = self.study_store.get_study(self.study_id)
        except Exception:  # noqa: BLE001
            return None
        return fresh.execution_status if fresh else None

    def _to_context(self) -> RunnerContext:
        """Create a RunnerContext for passing to extracted modules."""
        study = self._get_study()
        return RunnerContext(
            study_id=self.study_id,
            session=study.session_id,
            study=study,
            study_store=self.study_store,
            control=self.control,
            emit_fn=self._emit,
            goal_store=self._goal_store,
            prev_passed=self._prev_passed,
            best_score=self._best_score,
            idle_rounds=self._idle_rounds,
            total_used_time=self._total_used_time,
            total_used_turns=self._total_used_turns,
            trace_id=self._trace_id,
            plugin_registry=getattr(self, "_plugin_registry", None),
            loop_strategy=self._loop_strategy,
        )

    # ── public entrypoint ───────────────────────────────────────────

    async def run(self) -> str:
        sid = self._get_study().study_id
        session = self._get_study().session_id
        _dlog("runner", "run() starting study=%s session=%s max_rounds=%s",
              sid, session, self._get_study().max_rounds)
        # v4: bind the study-scoped trace for the whole lifecycle so
        # every log line + emitted event carries trace_id.
        from ..observability import bind_trace
        with bind_trace(trace_id=self._trace_id, study_id=sid):
            return await self._run_lifecycle(sid, session)

    async def _run_lifecycle(self, sid: str, session: str) -> str:
        self._emit(session, "study_started", {"study_id": sid, "round": self._get_study().current_round})
        reason = ShutdownReason.ERROR
        try:
            # v2 §15.2 recover: a MONITORING study restarts directly into
            # the monitor phase (no research rounds).
            if self._get_study().execution_status == StudyStatus.MONITORING:
                reason = await self._monitor_phase()
                return reason
            reason = await self._run_loop()
            # v2 §15.2: on E2 completion with monitoring enabled, enter the
            # post-completion monitor phase (rounds stop; periodic re-checks).
            # Runs in-sequence so the scheduler's control token and semaphore
            # stay alive for pause/resume/cancel during monitoring.
            if reason == ShutdownReason.TARGETS_MET and (
                self._get_study().monitor_interval_seconds or 0
            ) > 0:
                reason = await self._monitor_phase()
        except Exception as exc:
            _dlog("runner", "run() FAILED: study=%s error=%s", sid, exc)
            logger.exception("study %s failed: %s", sid, exc)
            tb = traceback.format_exc()
            # Honour any status another actor (e.g. scheduler.archive)
            # may have persisted while the round was in flight — don't
            # overwrite ARCHIVED with ERROR.
            live_status = self._current_db_status()
            if live_status == StudyStatus.ARCHIVED:
                _dlog(
                    "runner",
                    "run() exception suppressed: study=%s is ARCHIVED",
                    sid,
                )
            else:
                self.study_store.update_execution_status(
                    sid, StudyStatus.ERROR,
                    last_error=f"{type(exc).__name__}: {exc}"[:500],
                    last_traceback=tb[:8000],
                )
            self._emit(session, "study_failed", {"study_id": sid, "error": f"{type(exc).__name__}: {exc}"[:500], "reason": ShutdownReason.ERROR})
        finally:
            if self._own_goal_store:
                try:
                    self._goal_store.close()
                except Exception:
                    pass
            # Safety net: drop any background tasks this study left behind
            # on early/abnormal exits (round-end harvest covers the norm).
            try:
                from strategy_research.core.utils.bg_proc import harvest_by_owner
                harvest_by_owner(sid)
            except Exception:  # noqa: BLE001
                pass
            self._emit(session, "study_executor_stopped", {"study_id": sid, "reason": reason})
        return reason

    # ── main loop ──────────────────────────────────────────────────

    def _check_stop_conditions(
        self,
        result: dict,
        metrics: dict,
        verdict: str,
        round_num: int,
        session: str,
        sid: str,
    ) -> str | None:
        """Check post-round shutdown conditions; returns stop reason or None."""
        from strategy_research.core.study import state_store as _ss

        # targets met (E2: targets ∧ keep ∧ gates pass)
        if result.get("e2_passed"):
            self._complete_goal(result)
            self._mark_terminal(StudyStatus.COMPLETE, last_metrics=metrics, reason=ShutdownReason.TARGETS_MET)
            self._emit(session, "study_completed", {
                "study_id": sid, "goal_id": self._get_study().goal_id,
                "metrics": metrics, "round": round_num, "recap": verdict,
            })
            return ShutdownReason.TARGETS_MET

        # budget
        if self._budget_exceeded():
            self._mark_terminal(StudyStatus.BUDGET_LIMITED, last_metrics=metrics,
                                last_error=self._budget_summary(), reason=ShutdownReason.BUDGET)
            self._emit(session, "study_budget_limited", {
                "study_id": sid, "used": self._budget_summary(),
            })
            return ShutdownReason.BUDGET

        # stagnation
        decision = result.get("decision")
        if decision and isinstance(decision, dict) and decision.get("stagnation_triggered"):
            self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                last_error="stagnation", reason=ShutdownReason.STAGNATION)
            return ShutdownReason.STAGNATION

        # v2 review cycle stop (repeated deviation / review failure)
        review_stop = result.get("review_stop")
        if review_stop:
            if review_stop == ShutdownReason.REPEATED_DEVIATION:
                self._mark_terminal(
                    StudyStatus.ERROR, last_metrics=metrics,
                    last_error="repeated deviation (3x high)",
                    reason=ShutdownReason.REPEATED_DEVIATION,
                )
            else:
                self._mark_terminal(
                    StudyStatus.ERROR, last_metrics=metrics,
                    last_error=review_stop, reason=review_stop,
                )
            self._emit(session, "study_early_stopped", {
                "study_id": sid, "round": round_num, "reason": review_stop,
            })
            return review_stop

        # discard streak (design §8.2)
        _st = _ss.load(Path(self._get_study().workspace_path), sid)
        if _st.discard_streak >= SR_STUDY_MAX_DISCARD:
            self._mark_terminal(
                StudyStatus.ERROR, last_metrics=metrics,
                last_error=f"discard_streak={_st.discard_streak}",
                reason=ShutdownReason.DISCARD_STREAK,
            )
            self._emit(session, "study_early_stopped", {
                "study_id": sid, "round": round_num,
                "reason": ShutdownReason.DISCARD_STREAK,
            })
            return ShutdownReason.DISCARD_STREAK

        return None

    async def _run_loop(self) -> str:
        sid = self._get_study().study_id
        session = self._get_study().session_id

        # Load previous summary for cross-round context
        previous_summary = self._maybe_load_previous_summary(self.study)
        # v2: best score comes from state.json (keep-only, design §8.4)
        from strategy_research.core.study import state_store as ss
        state = ss.load(Path(self._get_study().workspace_path), sid)
        best_calmar = (state.best_metrics or {}).get("calmar")
        if best_calmar is not None:
            self._best_score = float(best_calmar)
        elif previous_summary and previous_summary.get("metrics"):
            self._best_score = previous_summary["metrics"].get("calmar", 0.0)

        round_num = self._get_study().current_round

        while True:
            if self.control.cancelled:
                # Honour any status another actor (e.g. scheduler.archive)
                # may have persisted while this iteration was in flight —
                # never overwrite an ARCHIVED row with CANCELLED.
                live_status = self._current_db_status()
                if live_status == StudyStatus.ARCHIVED:
                    self._emit(session, "study_cancelled", {
                        "study_id": sid,
                        "note": f"preserved live status={live_status.value}",
                    })
                    return ShutdownReason.CANCELLED
                self._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
                self._emit(session, "study_cancelled", {"study_id": sid})
                return ShutdownReason.CANCELLED

            if self.control.paused:
                self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
                self._emit(session, "study_paused", {"study_id": sid, "round": round_num})
                await self._wait_until_resumed()
                self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
                self._emit(session, "study_resumed", {"study_id": sid, "round": round_num})

            round_num += 1
            self._round_start_clock = time.perf_counter()

            # ── Flush pending objective replacements (Step B1) ───────
            # The user may have replaced the objective between rounds;
            # the store updated ``studies.objective`` already, but the
            # audit row is still flagged ``applied_round=NULL``. Mark
            # them applied here so the history UI can distinguish
            # pending vs applied. Also force-reload the study cache so
            # the freshly persisted objective is picked up.
            applied_now = self.study_store.mark_pending_objectives_applied(
                sid, round_num,
            )
            if applied_now:
                self.invalidate_study_cache()
                self._emit(session, "study_objective_applied", {
                    "study_id": sid,
                    "round": round_num,
                    "count": applied_now,
                })

            # Consume directives
            pending = self.study_store.list_pending_directives(sid)
            directive_text = self._format_directives(pending) if pending else None

            _dlog("loop", "round %d start study=%s", round_num, sid)

            # ── Run one round (overridable for tests) ──────────────
            result = await asyncio.to_thread(
                self._run_one_round, round_num, previous_summary, directive_text,
            )
            _dlog("loop", "round %d result: aborted=%s, paused=%s, metrics=%s, verdict=%s",
                  round_num, result.get("aborted"), result.get("paused_for_approval"),
                  bool(result.get("metrics")), result.get("verdict"))

            # Mark directives consumed
            if pending:
                self.study_store.mark_directives_consumed(sid, [d.directive_id for d in pending])

            # Handle aborted round (novelty rejected)
            if result.get("aborted"):
                _dlog("loop", "round %d aborted: %s", round_num, result.get("reason"))
                continue

            # P4: Handle HITL approval pause
            if result.get("paused_for_approval"):
                _dlog("loop", "round %d paused for HITL approval", round_num)
                self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
                self._emit(session, "study_paused", {
                    "study_id": sid, "round": round_num,
                    "reason": "hitl_approval",
                    "interrupt_id": result.get("interrupt_id"),
                    "hypothesis": result.get("hypothesis"),
                })
                # Poll for approval (max 10 minutes, check every 5 seconds)
                decision = await self._wait_for_approval(sid, round_num, timeout_s=600)
                if decision != "approved":
                    # Timeout or rejection — skip this round and keep the
                    # study loop alive (status must leave PAUSED).
                    self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
                    self._emit(session, "study_round_rejected", {
                        "study_id": sid, "round": round_num,
                        "reason": f"hitl_{decision}",
                    })
                    continue
                # Resume the round from checkpoint
                self.study_store.update_execution_status(sid, StudyStatus.RUNNING)
                # Reconstruct context from study object (same as _run_one_round):
                # read state against the run's own strategy copy so langgraph
                # agents see exactly what the interrupted round was seeing.
                study = self._get_study()
                _path = Path(study.workspace_path).resolve()
                _strategy = study.strategy_name
                _graph = self._load_graph(_path, sid)
                from strategy_research.core.autoresearch import read_current_state
                from strategy_research.core.study import round_manifest as rm
                _round_dir = rm.round_dir(_path, sid, round_num)
                _run_dirs = sorted(
                    d for d in _round_dir.iterdir()
                    if d.is_dir() and d.name.startswith("run_")
                ) if _round_dir.exists() else []
                if _run_dirs:
                    _run_dir = _run_dirs[-1]
                else:
                    logger.warning(
                        "HITL resume: no run_* dirs under %s, falling back to run_0001",
                        _round_dir,
                    )
                    _run_dir = _round_dir / "run_0001"
                _current_state = read_current_state(
                    _path, _strategy,
                    strategy_file=_run_dir / "strategy.py",
                )
                _current_state["study_strategy_path"] = str(
                    _run_dir.relative_to(_path) / "strategy.py"
                )
                result = await asyncio.to_thread(
                    self._resume_round_langgraph, _path, _strategy,
                    _current_state, _run_dir, _graph,
                    session=session, sid=sid, round_num=round_num,
                    directive_text=directive_text,
                )
                if result.get("aborted"):
                    continue

            metrics = result.get("metrics", {})
            verdict = result.get("verdict", "discard")
            summary = result.get("summary")
            previous_summary = summary or previous_summary

            # ── budget ─────────────────────────────────────────────
            self._account_round_budget(result)
            self.study_store.update_round_heartbeat(sid, round_num)
            self.study_store.update_last_metrics(sid, metrics, verdict)

            # ── round end: harvest this study's background tasks ──
            # A backgrounded agent tool (run_backtest background=True /
            # run_bg_command) abandoned mid-poll must not linger into the
            # next round (kill live ones, drop finished ones).
            from strategy_research.core.utils.bg_proc import harvest_by_owner
            killed = harvest_by_owner(sid)
            if killed:
                _dlog("loop", "round %d: harvested %d stale bg tasks", round_num, killed)

            # ── SSE: study_round ───────────────────────────────────
            self._emit(session, "study_round", {
                "study_id": sid, "round": round_num,
                "run": result.get("run_name", ""),
                "metrics": metrics, "verdict": verdict,
            })

            # ── SSE: study_budget (per-round accounting) ─────────
            self._emit(session, "study_budget", {
                "study_id": sid,
                "budget": {
                    "budget_used_turns": self._total_used_turns,
                    "budget_used_time_s": round(self._total_used_time, 1),
                    "budget_turn": self._get_study().budget_turn,
                    "budget_time_seconds": self._get_study().budget_time_seconds,
                },
            })

            # ── SSE: study_scoreboard (lever precision) ──────────
            if hasattr(self, "_scoreboard"):
                sb = self._scoreboard.get_scoreboard()
                sb_data = [
                    {"lever": s.lever, "attempts": s.attempts,
                     "accepted": s.accepted, "reverted": s.reverted}
                    for s in sb if s.attempts > 0
                ]
                if sb_data:
                    self._emit(session, "study_scoreboard", {
                        "study_id": sid,
                        "round": round_num,
                        "scoreboard": sb_data,
                    })

            # ── shutdown conditions (targets/budget/stagnation/review/discard) ──
            stop_reason = self._check_stop_conditions(
                result, metrics, verdict, round_num, session, sid
            )
            if stop_reason is not None:
                return stop_reason

            # ── shutdown: max_rounds ───────────────────────────────
            if self._get_study().max_rounds is not None and round_num >= self._get_study().max_rounds:
                self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                    last_error=f"max_rounds={self._get_study().max_rounds}", reason=ShutdownReason.MAX_ROUNDS)
                return ShutdownReason.MAX_ROUNDS

            # ── AEGIS: Early-stop (only when max_rounds is configured) ──
            if self._get_study().max_rounds is not None:
                current_score = metrics.get("calmar", 0.0) or 0.0
                if current_score > self._best_score:
                    self._best_score = current_score
                    self._idle_rounds = 0
                else:
                    self._idle_rounds += 1
                if self._idle_rounds >= self._get_study().early_stop_patience:
                    self._mark_terminal(StudyStatus.EARLY_STOPPED, last_metrics=metrics,
                                        last_error=f"idle={self._idle_rounds} rounds, best={self._best_score}",
                                        reason=ShutdownReason.EARLY_STOPPED)
                    self._emit(session, "study_early_stopped", {
                        "study_id": sid, "round": round_num,
                        "idle_rounds": self._idle_rounds, "best_score": self._best_score,
                    })
                    return ShutdownReason.EARLY_STOPPED

            self._prev_passed = result.get("passed_now", set())

            # ── cooldown ───────────────────────────────────────────
            cooldown = self._round_cooldown()
            _dlog("loop", "cooldown %.1fs before round %d", cooldown, round_num + 1)
            await asyncio.sleep(cooldown)

    # ── v2 monitor phase (design §15) ──────────────────────────────

    async def _monitor_phase(self) -> str:
        """Post-completion monitoring — delegates to monitor.py."""
        from .monitor import monitor_phase
        return await monitor_phase(self)

    def _run_one_round(
        self,
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Execute one round: phases + AEGIS hooks + v2 artifacts.

        v2 (design §8.1): per-round autonomous directory under
        ``study/<id>/rounds/round_NNNN/``; the round starts from the
        adopted run (last keep run, else baseline) copied into the round's
        first run dir; three artifacts (manifest/summary.md/journal.md)
        land at round end (phase 1); state.json is the authority.

        Overridable for tests to stub round execution.
        """
        from ..observability import bind_trace, get_trace_context

        # v4: ensure a trace_id exists for the round (study-scoped stable
        # id: generated once per study, reused across rounds so all rounds
        # correlate to the same research line). Outer attempts may already
        # have bound one.
        outer = get_trace_context()
        with bind_trace(
            trace_id=outer.get("trace_id") or self._trace_id,
            study_id=self._get_study().study_id,
            round_num=round_num,
        ):
            return self._run_one_round_impl(
                round_num, previous_summary, directive_text,
            )

    def _run_one_round_impl(
        self,
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Actual round implementation — delegates to phase_engine.py."""
        from .phase_engine import run_round_phases
        return run_round_phases(self, round_num, previous_summary, directive_text)

    # ── v2 review cycle (design §10) ────────────────────────────────

    def _check_guidance_gates(self, guidance: Any, metrics: dict) -> list[dict]:
        """Hard-check guidance gates; returns violating gate dicts (empty = pass)."""
        from strategy_research.core.study import guidance as gd

        violations: list[dict] = []
        if not guidance.gates:
            return violations
        found, skipped = gd.check_violations(guidance.gates, metrics)
        for gid in skipped:
            logger.warning(
                "guidance gate %s skipped (metric missing): %s", gid, metrics,
            )
        return found if found else violations

    def _novelty_gate(
        self, round_num: int, hypothesis: str, predicted_affected: list
    ) -> bool:
        """Run the AEGIS novelty gate; returns True when the round proceeds."""
        is_novel, novelty_reason = self._check_novelty(hypothesis, predicted_affected)
        if is_novel:
            return True
        self._archive_rejected(round_num, hypothesis, "novelty", novelty_reason)
        self._emit(self._get_study().session_id, "study_round_rejected", {
            "study_id": self._get_study().study_id, "round": round_num,
            "reason": "novelty", "detail": novelty_reason,
        })
        return False

    def _record_journal_and_regression(
        self,
        round_num: int,
        hypothesis: str,
        predicted_affected: list,
        lever: str,
        strategist_output,
        gating_outcome: str,
        attribution,
    ) -> None:
        """AEGIS: append journal entry + run the regression gate."""
        session = self._get_study().session_id
        self._goal_store.append_journal_entry(
            self._get_study().goal_id, session, round_num, hypothesis, hypothesis[:60],
            levers=[lever], predicted_affected=predicted_affected,
            changeset=strategist_output.get("changes") if isinstance(strategist_output, dict) else None,
        )
        self._goal_store.fill_journal_attribution(
            self._get_study().goal_id, session, round_num, gating_outcome, attribution,
        )
        passes, regressed = self._check_regression(attribution)
        if not passes:
            self._archive_rejected(round_num, hypothesis, "regression", str(regressed))
            self._emit(session, "study_round_rejected", {
                "study_id": self._get_study().study_id, "round": round_num,
                "reason": "regression", "regressed": regressed,
            })

    def _update_round_state(
        self,
        path: Path,
        sid: str,
        round_num: int,
        run_name: str,
        verdict: str,
        metrics: dict,
        state: Any,
        strategy_changes,
    ) -> None:
        """Persist state.json + DB mirror for the completed round."""
        from strategy_research.core.study import state_store as ss

        state.last_completed_round = round_num
        if verdict == "keep":
            state.last_keep_run_dir = f"rounds/round_{round_num:04d}/{run_name}"
            state.discard_streak = 0
            for key in ("calmar", "sharpe", "max_dd"):
                if key in metrics and isinstance(metrics.get(key), (int, float)):
                    if (state.best_metrics.get(key, float("-inf")) or float("-inf")) < metrics[key]:
                        state.best_metrics[key] = metrics[key]
        else:
            state.discard_streak += 1
        state.budget_used_turns = self._total_used_turns
        state.budget_used_time_s = round(self._total_used_time, 1)
        ss.save(path, sid, state)

        # DB mirror: study_rounds row (phase 1 body). File-first: the
        # state.json write above is authoritative; the DB mirror is
        # best-effort but retried once so a transient SQLite lock does
        # not leave the UI permanently behind (H1).
        try:
            self.study_store.append_round(
                sid, round_num, run_name,
                metrics=metrics, verdict=verdict,
                config_changes=strategy_changes,
            )
        except Exception as exc:  # noqa: BLE001 — file-first; DB is mirror
            logger.warning("append_round failed (mirror, retry): %s", exc)
            try:
                self.study_store.append_round(
                    sid, round_num, run_name,
                    metrics=metrics, verdict=verdict,
                    config_changes=strategy_changes,
                )
            except Exception as exc2:  # noqa: BLE001 — keep going
                logger.warning("append_round retry failed (mirror): %s", exc2)

    def _run_review_cycle(
        self,
        round_num: int,
        manifest: dict,
        state: Any,
        verdict: str,
        hypothesis: str,
    ) -> str | None:
        """Inter-round review: deviation tracking, info collection, todos.

        Runs synchronously inside the round (occupies the semaphore slot,
        design §10.2). Returns a stop reason when the study must halt
        (repeated high deviation / repeated review failure), else None.
        """
        from strategy_research.core.agent.role_factory import (
            run_agent_via_llm,
            should_use_real_llm,
        )
        from strategy_research.core.study import review_loop as rl
        from strategy_research.core.study import round_manifest as rm
        from strategy_research.core.study import state_store as ss

        sid = self._get_study().study_id
        session = self._get_study().session_id
        path = Path(self._get_study().workspace_path).resolve()
        root = ss.study_root(path, sid)
        todos_path = root / "todos.md"
        knowledge_path = root / "knowledge.md"
        archive_path = root / "knowledge-archive.md"

        # ── ① reviewer ─────────────────────────────────────────────
        knowledge_text = (
            knowledge_path.read_text(encoding="utf-8")
            if knowledge_path.exists() else ""
        )
        review_input = (
            f"objective: {self._get_study().objective}\n"
            f"metric_targets: {json.dumps(self._get_study().metric_targets, ensure_ascii=False)}\n"
            f"round: {round_num}\n"
            f"verdict: {verdict}\n"
            f"hypothesis: {hypothesis}\n"
            f"manifest: {json.dumps(manifest, ensure_ascii=False, default=str)[:4000]}\n"
            f"last_review: {json.dumps(state.last_review, ensure_ascii=False, default=str)}\n"
            f"continuous_deviation: {state.continuous_deviation}\n"
            f"todos:\n{todos_path.read_text(encoding='utf-8') if todos_path.exists() else ''}\n"
            f"knowledge (recent):\n{knowledge_text[-3000:]}\n"
        )
        use_real = self._get_study().behavior is None and should_use_real_llm()
        raw_review = ""
        try:
            if use_real:
                raw_review = run_agent_via_llm(
                    role="study_reviewer",
                    workspace_path=path,
                    strategy_name=self._get_study().strategy_name,
                    task=review_input,
                    max_iterations=3,
                    loop_strategy=self._loop_strategy,
                )
            else:
                raw_review = json.dumps({
                    "deviation": "low", "deviation_reason": "stub",
                    "info_gap": False, "topics": [],
                    "todo_updates": [], "next_focus": "",
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("study_reviewer failed: %s", exc)
            state.review_fail_count += 1
            ss.save(path, sid, state)
            if state.review_fail_count >= 2:
                return ShutdownReason.REVIEW_FAILED
            return None

        review = rl.normalize_review(rl.parse_review_output(raw_review))
        if not raw_review.strip() or not review.get("next_focus"):
            # empty/failed review output → skip (design §10.3)
            state.review_fail_count += 1
            ss.save(path, sid, state)
            if state.review_fail_count >= 2:
                return ShutdownReason.REVIEW_FAILED
            return None
        state.review_fail_count = 0

        # ── ② information collection ───────────────────────────────
        if rl.should_collect(
            info_gap=review["info_gap"],
            round_num=round_num,
            last_collect_round=state.last_collect_round,
            collect_interval=SR_STUDY_COLLECT_INTERVAL,
        ):
            topics = review["topics"] or [self._get_study().objective[:80]]
            self._collect_knowledge(topics)

        # ── ③ todos application ────────────────────────────────────
        applied = rl.apply_todos(
            todos_path, review["todo_updates"], self._get_study().objective,
        )
        if applied:
            self._emit(session, "study_todos_updated", {
                "study_id": sid, "updates": review["todo_updates"],
            })

        # ── ④ deviation state + stop guard ─────────────────────────
        if review["deviation"] == "high":
            state.continuous_deviation += 1
        else:
            state.continuous_deviation = 0
        state.last_review = {
            "round": round_num, **review,
        }
        ss.save(path, sid, state)
        self._emit(session, "study_review", {
            "study_id": sid, "round": round_num,
            "deviation": review["deviation"], "info_gap": review["info_gap"],
        })

        # ── ⑤ manifest phase 2: review overlay + DB mirror ─────────
        manifest = rm.overlay_review(manifest, review)
        rm.save_manifest(manifest, path, sid, round_num)
        try:
            self.study_store.update_round(sid, round_num, review)
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_round failed (mirror): %s", exc)

        # knowledge compaction (plan B rule prescreen; design §11.3)
        try:
            compacted = rl.maybe_compact(
                knowledge_path, archive_path=archive_path,
            )
            if compacted:
                self._emit(session, "study_knowledge_compacted", {
                    "study_id": sid, **compacted,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("knowledge compaction failed: %s", exc)

        # ── ⑥ stop guard: overlay recorded even on the stop round ─
        if state.continuous_deviation >= SR_STUDY_MAX_DEVIATION:
            return ShutdownReason.REPEATED_DEVIATION
        return None

    def _run_execution_with_parse_retry(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        researcher_output: dict,
        run_dir: Path,
        *,
        session: str,
        run_name: str,
        results_tsv: Path,
        round_num: int,
    ) -> dict:
        """Wrap ``run_execution_phase`` with parse_failed auto-recovery.

        The execution phase spawns 4 agents (data_quality, factor_analyst,
        strategist, portfolio_construction). If any of them return
        ``parse_failed`` (LLM JSON-parse failure or repeated identical
        tool calls beyond the approval gate), the entire round would
        be wasted. This helper detects that and retries the whole round up
        to `` ``SR_AGENT_MAX_PARSE_RETRIES`` times with exponential
        backoff. A persistent failure surfaces as a normal ``discard``
        round that consumes one idle round (early-stop aware).
        """
        from ..autoresearch import run_execution_phase

        last_failed_agents: list[str] = []

        for attempt in range(SR_AGENT_MAX_PARSE_RETRIES):
            result = run_execution_phase(
                path, strategy, current_state, researcher_output, run_dir,
                session_id=session, run_name=run_name,
                behavior=self._get_study().behavior, max_retries=3,
                max_iterations=SR_AGENT_MAX_ITER,
                strategy_dir=run_dir,
                results_tsv=results_tsv,
                round_num=round_num,
                loop_strategy=self._loop_strategy,
            )
            last_failed_agents = _parse_failed_agents(result)
            if not last_failed_agents:
                return result
            # Partial success: some agents produced valid output — keep
            # the working ones, only retry if no strategist output exists.
            if result.get("strategist_output"):
                logger.warning(
                    "Round %d partial parse_failed (%s) but strategist OK; "
                    "continuing without retry",
                    round_num, last_failed_agents,
                )
                return result
            if attempt < SR_AGENT_MAX_PARSE_RETRIES - 1:
                delay = min(
                    SR_AGENT_PARSE_BACKOFF_BASE * (2 ** attempt),
                    SR_AGENT_PARSE_BACKOFF_MAX,
                )
                logger.warning(
                    "Round %d parse_failed agents=%s, retrying in %ds "
                    "(attempt %d/%d)",
                    round_num, last_failed_agents, delay,
                    attempt + 1, SR_AGENT_MAX_PARSE_RETRIES,
                )
                self._emit(session, "study_parse_retry", {
                    "study_id": self._get_study().study_id,
                    "round": round_num,
                    "failed_agents": last_failed_agents,
                    "delay_s": delay,
                    "attempt": attempt + 1,
                    "max_attempts": SR_AGENT_MAX_PARSE_RETRIES,
                })
                time.sleep(delay)

        # Exhausted retries — count as idle to trigger early stop.
        logger.error(
            "Round %d parse_failed after %d retries (%s); counting as idle",
            round_num, SR_AGENT_MAX_PARSE_RETRIES, last_failed_agents,
        )
        self._idle_rounds += 1
        return result

    # ── Graph loading (topology-aware scheduling) ────────────────

    def _load_graph(self, path: Path, sid: str) -> "StudyGraph":
        """Load the study's persisted execution graph.

        Falls back to ``DEFAULT_STANDARD_GRAPH`` when ``graph.json`` is
        missing or malformed (legacy studies pre-migration). Validates
        and logs warnings; never raises — a broken graph must not block
        a running study.
        """
        from .graph import StudyGraph
        from .graph_templates import DEFAULT_STANDARD_GRAPH

        graph = StudyGraph.load(path, sid)
        if graph is None:
            graph = DEFAULT_STANDARD_GRAPH
        errors = graph.validate()
        if errors:
            logger.warning(
                "Study %s graph validation warnings: %s; "
                "falling back to standard template",
                sid, errors,
            )
            graph = DEFAULT_STANDARD_GRAPH
        return graph

    # ── DAG-driven round execution (P5 unified engine) ────────

    def _run_round_via_dag(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        run_dir: Path,
        graph: "StudyGraph",
        *,
        session: str,
        sid: str,
        round_num: int,
        directive_text: str | None,
    ) -> dict:
        """Execute one round via DAG engine (delegated to dag_engine.py)."""
        from .dag_engine import run_round_dag
        return run_round_dag(
            self, path, strategy, current_state, run_dir, graph,
            session=session, sid=sid, round_num=round_num,
            directive_text=directive_text,
        )

    def _run_round_via_langgraph(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        run_dir: Path,
        graph: "StudyGraph",
        *,
        session: str,
        sid: str,
        round_num: int,
        directive_text: str | None,
    ) -> dict:
        """Execute one round via LangGraph engine.

        Requires ``langgraph`` extra: ``pip install strategy-research[langgraph]``.
        Falls back to phase engine if langgraph is not installed.

        P4: HITL is enabled by default for langgraph engine. The novelty
        gate interrupt pauses execution for human approval of the
        researcher's hypothesis.
        """
        try:
            from .langgraph_engine import run_round_langgraph
        except ImportError:
            logger.warning(
                "langgraph extra not installed; falling back to phases engine. "
                "Install with: pip install strategy-research[langgraph]"
            )
            raise RuntimeError(
                "langgraph engine selected but langgraph package not installed. "
                "Install with: pip install strategy-research[langgraph]"
            )

        # P6: Determine profile from engine field
        profile_name = getattr(self._get_study(), "engine", None) or "langgraph"
        from .langgraph_engine import get_profile
        profile = get_profile(profile_name)

        return run_round_langgraph(
            runner=self,
            path=path,
            strategy=strategy,
            current_state=current_state,
            run_dir=run_dir,
            graph=graph,
            session=session,
            sid=sid,
            round_num=round_num,
            directive_text=directive_text,
            profile=profile,
        )

    async def _wait_for_approval(
        self, sid: str, round_num: int, timeout_s: int = 600
    ) -> str:
        """Poll for HITL approval.

        Returns the resolved decision: ``"approved"``, ``"rejected"``,
        or ``"timeout"`` when no response arrived within ``timeout_s``.
        """
        import asyncio
        start = time.time()
        while time.time() - start < timeout_s:
            interrupt = self.study_store.get_interrupt_for_round(sid, round_num)
            if interrupt is not None and interrupt.status in ("approved", "rejected"):
                return interrupt.status
            await asyncio.sleep(5)
        return "timeout"

    def _resume_round_langgraph(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        run_dir: Path,
        graph: "StudyGraph",
        *,
        session: str,
        sid: str,
        round_num: int,
        directive_text: str | None,
    ) -> dict:
        """Resume a HITL-paused round from checkpoint."""
        try:
            from .langgraph_engine import resume_round_langgraph
        except ImportError:
            raise RuntimeError("langgraph package not installed")

        # Same profile resolution as _run_round_via_langgraph — the
        # rebuilt graph must include the HITL gate node the checkpoint
        # stopped at, otherwise resume fails or silently skips gating.
        profile_name = getattr(self._get_study(), "engine", None) or "langgraph"
        from .langgraph_engine import get_profile
        profile = get_profile(profile_name)

        return resume_round_langgraph(
            runner=self,
            path=path,
            strategy=strategy,
            current_state=current_state,
            run_dir=run_dir,
            graph=graph,
            session=session,
            sid=sid,
            round_num=round_num,
            directive_text=directive_text,
            profile=profile,
        )

    def _layered_topological_layers(self, graph) -> list[list[str]]:
        """Fallback topological layer computation if AgentDAGConfig
        doesn't expose one (kept for forward compat)."""
        return graph.topological_layers()

    @staticmethod
    def _try_parse_json(text: str) -> Any:
        """Parse agent output JSON; return the raw string on failure."""
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def _build_round_task_text(
        self, current_state: dict, directive_text: str | None,
    ) -> str:
        """Compose the base task text for every agent in the round."""
        from ..autoresearch import read_current_state
        parts: list[str] = ["根据研究目标与历史结果完成当前轮次的工作。"]
        if current_state:
            parts.append("## 当前状态\n" + json.dumps(
                current_state, ensure_ascii=False, default=str,
            ))
        if directive_text:
            parts.append("## 用户指令\n" + directive_text)
        return "\n\n".join(parts)

    def _save_agent_output(
        self, run_dir: Path, agent_id: str, result: Any,
    ) -> None:
        """Persist an agent's output to ``run_dir/<agent_id>.json``."""
        from ..autoresearch import save_agent_record
        try:
            # result is a dict from save_agent_outputs, not an object
            output_data = result.get("output", result) if isinstance(result, dict) else result
            save_agent_record(
                run_dir, agent_id, 3, {}, output_data,
            )
        except Exception:  # noqa: BLE001
            logger.debug("save_agent_record failed for %s", agent_id)

    def _rebuild_phase_outputs(
        self, agent_outputs: dict[str, Any], graph,
    ) -> dict:
        """Translate the DAG ``agent_outputs`` map back into the
        legacy ``run_execution_phase`` / ``run_evaluation_phase``
        result schema that downstream callers consume.
        """
        researcher_output = agent_outputs.get("researcher") or {}
        # An agent that hits max_iterations yields a plain-text answer
        # ("Reached max_iterations=... without a final answer.") instead
        # of the expected JSON action object. Parse JSON strings when
        # possible; fall back to {} so downstream .get() calls don't
        # crash with AttributeError (same pattern as backtest/decide
        # handling below).
        if isinstance(researcher_output, str):
            try:
                parsed = json.loads(researcher_output)
                researcher_output = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                researcher_output = {}
        elif not isinstance(researcher_output, dict):
            researcher_output = {}
        backtest_raw = agent_outputs.get("backtest") or {}
        if isinstance(backtest_raw, str):
            try:
                backtest_raw = json.loads(backtest_raw)
            except (json.JSONDecodeError, TypeError):
                backtest_raw = {}
        metrics = (
            backtest_raw.get("metrics", {}) if isinstance(backtest_raw, dict)
            else {}
        )
        backtest_result = (
            backtest_raw if isinstance(backtest_raw, dict) else {}
        )
        backtest_error = (
            backtest_raw.get("error") if isinstance(backtest_raw, dict)
            and not backtest_raw.get("success") else None
        )

        decision_raw = agent_outputs.get("decide") or {}
        if isinstance(decision_raw, str):
            try:
                decision_raw = json.loads(decision_raw)
            except (json.JSONDecodeError, TypeError):
                decision_raw = {}
        verdict = "discard"
        if isinstance(decision_raw, dict):
            verdict = decision_raw.get("verdict") or decision_raw.get(
                "decision", "discard",
            )

        # Lightweight decision stub: downstream calls .to_dict().
        from ..strategy_acceptance import (
            AcceptanceDecision,
            decide,
        )
        try:
            decision_obj = decide(metrics=metrics, llm_verdict=None)
        except Exception:  # noqa: BLE001
            decision_obj = AcceptanceDecision(verdict=verdict, reason="")

        return {
            "researcher_output": researcher_output,
            "data_quality_output": agent_outputs.get("data_quality") or {},
            "factor_analyst_output": agent_outputs.get("factor_analyst") or {},
            "strategist_output": agent_outputs.get("strategist") or {},
            "portfolio_construction_output": (
                agent_outputs.get("portfolio_construction") or {}
            ),
            "backtest_result": backtest_result,
            "backtest_error": backtest_error,
            "metrics": metrics,
            "risk_controller_output": agent_outputs.get("risk_controller") or {},
            "attribution_analyst_output": (
                agent_outputs.get("attribution_analyst") or {}
            ),
            "anti_overfit_analyst_output": (
                agent_outputs.get("anti_overfit_analyst") or {}
            ),
            "backtest_diagnostics_output": (
                agent_outputs.get("backtest_diagnostics") or {}
            ),
            "decision": decision_obj,
            "verdict": verdict,
            "aoa_llm_verdict": (
                agent_outputs.get("anti_overfit_analyst") or {}
            ),
        }

    def _emit_topology(
        self,
        session: str,
        sid: str,
        round_num: int,
        graph: "StudyGraph",
        agent_outputs: dict,
    ) -> None:
        """Emit SSE events describing the current layer being processed.

        The frontend uses these events to highlight which layer is
        active and to flag nodes that errored (so they can show a red
        border).
        """
        layers = graph.topological_layers()
        completed_ids = {
            nid for nid, out in agent_outputs.items()
            if out and not (isinstance(out, dict) and out.get("error"))
        }
        for layer_idx, layer_ids in enumerate(layers):
            for nid in layer_ids:
                node = graph.node_map.get(nid)
                if node is None:
                    continue
                status = (
                    "completed" if nid in completed_ids
                    else "pending"
                )
                self._emit(session, "study_graph_node", {
                    "study_id": sid,
                    "round": round_num,
                    "layer": layer_idx,
                    "node_id": nid,
                    "node_type": node.type,
                    "node_label": node.label or nid,
                    "enabled": node.enabled,
                    "status": status,
                })

    def _collect_knowledge(self, topics: list[str]) -> int:
        """v2: run the collector agent and append to knowledge.md.

        Returns the number of appended entries (0 on stub/failure).
        """
        from strategy_research.core.agent.role_factory import (
            run_agent_via_llm,
            should_use_real_llm,
        )
        from strategy_research.core.study import review_loop as rl
        from strategy_research.core.study import state_store as ss

        sid = self._get_study().study_id
        path = Path(self._get_study().workspace_path).resolve()
        root = ss.study_root(path, sid)
        knowledge_path = root / "knowledge.md"
        knowledge_text = (
            knowledge_path.read_text(encoding="utf-8")
            if knowledge_path.exists() else ""
        )
        try:
            if self._get_study().behavior is None and should_use_real_llm():
                raw_entries = run_agent_via_llm(
                    role="study_collector",
                    workspace_path=path,
                    strategy_name=self._get_study().strategy_name,
                    task=(
                        f"objective: {self._get_study().objective}\n"
                        f"topics: {json.dumps(topics, ensure_ascii=False)}\n"
                        f"existing knowledge:\n{knowledge_text[-2000:]}\n"
                    ),
                    max_iterations=3,
                    loop_strategy=self._loop_strategy,
                )
                entries = rl.parse_review_output(raw_entries)
                if isinstance(entries, dict):
                    entries = [entries]
            else:
                entries = []
            collected = rl.append_knowledge(
                knowledge_path, entries, self._get_study().objective,
            )
            state = ss.load(path, sid)
            state.last_collect_round = state.last_completed_round or 0
            ss.save(path, sid, state)
            if collected:
                self._emit(sid, "study_knowledge_update", {
                    "study_id": sid, "entries_added": collected,
                })
            return collected
        except Exception as exc:  # noqa: BLE001
            logger.warning("collector failed: %s", exc)
            return 0

    def _record_keep_evidence(self, round_num: int, run_name: str, metrics: dict) -> None:
        """E1: keep rounds append goal evidence + emit progress (design §16.3)."""
        sid = self._get_study().study_id
        session = self._get_study().session_id
        try:
            from strategy_research.core.goal import EvidenceInput
            ev = self._goal_store.append_evidence(
                session_id=session,
                goal_id=self._get_study().goal_id,
                expected_goal_id=self._get_study().goal_id,
                evidence=EvidenceInput(
                    text=(
                        f"Study round {round_num} keep — {run_name}: "
                        f"Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')} "
                        f"MaxDD={metrics.get('max_dd')}"
                    ),
                    evidence_type="study_keep", run_id=run_name,
                    source_provider="study", source_type="round_keep",
                ),
            )
            self._emit(session, "study_evidence", {
                "study_id": sid, "evidence_id": ev.evidence_id,
                "run": run_name,
            })
            criteria = self._goal_store.list_criteria(self._get_study().goal_id)
            evidence = self._goal_store.list_evidence(self._get_study().goal_id)
            seen = {e.criterion_id for e in evidence if e.criterion_id}
            covered = sum(1 for c in criteria if c.criterion_id in seen)
            total = len(criteria)
            self._emit(session, "study_progress", {
                "study_id": sid, "covered": covered, "total": total,
                "percent": round(covered / total * 100) if total else 100,
            })
            # ── SSE: study_goal_snapshot (real-time goal state) ──
            snap = self._goal_store.get_goal_snapshot(
                self._get_study().goal_id,
            )
            if snap:
                g = snap.get("goal", {}) or {}
                self._emit(session, "study_goal_snapshot", {
                    "study_id": sid,
                    "goal_snapshot": {
                        "goal_id": g.get("goal_id"),
                        "goal_status": g.get("status"),
                        "objective": g.get("objective"),
                        "progress_percent": g.get("progress_percent", 0),
                        "evidence_count": snap.get("evidence_count", 0),
                        "criteria": [
                            {"criterion_id": c.get("criterion_id"), "text": c.get("text"),
                             "status": c.get("status"), "required": c.get("required", True)}
                            for c in snap.get("criteria", []) or []
                        ],
                    },
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("study %s keep evidence failed: %s", sid, exc)

    def _verdict_reason(self, eval_result: dict, strategist_output: Any) -> str:
        """Extract the verdict reason from decision/attribution output."""
        decision = eval_result.get("decision")
        if decision is not None:
            reason = getattr(decision, "reason", None) or \
                (decision.get("reason", "") if isinstance(decision, dict) else "")
            if reason:
                return str(reason)
        aoa = eval_result.get("aoa_llm_verdict")
        if isinstance(aoa, dict) and aoa.get("decision") == "discard":
            return str(aoa.get("reason", "anti-overfit rejection"))
        return ""

    # ── AEGIS helpers ──────────────────────────────────────────────

    def _check_novelty(self, hypothesis: str, predicted_affected: list[str]) -> tuple[bool, str | None]:
        if not self._get_study().goal_id:
            return True, None
        return self._goal_store.check_novelty(
            self._get_study().goal_id, hypothesis, [], predicted_affected,
        )

    def _check_regression(self, attribution: dict[str, str]) -> tuple[bool, list[str]]:
        if not self._get_study().goal_id:
            return True, []
        return self._goal_store.check_regression(self._get_study().goal_id, attribution)

    def _archive_rejected(self, round_num: int, hypothesis: str, reason: str, detail: str) -> None:
        if not self._get_study().goal_id:
            return
        self._goal_store.archive_rejected_edit(
            self._get_study().goal_id, round_num, hypothesis, reason, detail,
        )

    def _build_journal_context(self) -> str:
        if not self._get_study().goal_id:
            return ""
        return self._goal_store.build_journal_context(self._get_study().goal_id, self._get_study().current_round)

    def _build_scoreboard_context(self) -> str:
        if not hasattr(self, "_scoreboard"):
            return ""
        return self._scoreboard.build_scoreboard_context()

    # ── budget ─────────────────────────────────────────────────────

    def _account_round_budget(self, exec_result: dict) -> None:
        if self._round_start_clock is not None:
            self._total_used_time += time.perf_counter() - self._round_start_clock
        outs = exec_result.get("agent_outputs") or {}
        self._total_used_turns += sum(1 for v in outs.values() if v and not (isinstance(v, dict) and v.get("error")))

    def _budget_exceeded(self) -> bool:
        s = self.study
        if s.budget_time_seconds is not None and self._total_used_time >= s.budget_time_seconds:
            return True
        if s.budget_turn is not None and self._total_used_turns >= s.budget_turn:
            return True
        return False

    def _budget_summary(self) -> str:
        return f"turns_used={self._total_used_turns}, time_used={self._total_used_time:.1f}s"

    # ── goal completion ────────────────────────────────────────────

    def _complete_goal(self, exec_result: dict) -> None:
        if not self._get_study().goal_id:
            return
        try:
            from strategy_research.core.goal import EvidenceInput
            metrics = exec_result.get("metrics", {})
            run_name = exec_result.get("run_name", "")
            existing = self._goal_store.list_evidence(self._get_study().goal_id)
            seen = {ev.criterion_id for ev in existing if ev.criterion_id}
            criteria = self._goal_store.list_criteria(self._get_study().goal_id)
            for c in criteria:
                if not c.required or c.criterion_id in seen:
                    continue
                self._goal_store.append_evidence(
                    session_id=self._get_study().session_id,
                    goal_id=self._get_study().goal_id,
                    expected_goal_id=self._get_study().goal_id,
                    evidence=EvidenceInput(
                        text=f"Study 达标自动覆盖 — {run_name}: Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')} MaxDD={metrics.get('max_dd')}",
                        criterion_id=c.criterion_id, evidence_type="acceptance",
                        run_id=run_name, source_provider="study", source_type="metric_targets_met",
                    ),
                )
            self._goal_store.complete_lite(
                session_id=self._get_study().session_id, goal_id=self._get_study().goal_id,
                expected_goal_id=self._get_study().goal_id,
                recap=f"研究达标 — Calmar={metrics.get('calmar')}, Sharpe={metrics.get('sharpe')}, MaxDD={metrics.get('max_dd')}",
            )
        except Exception as exc:
            logger.exception("study %s goal completion failed: %s", self._get_study().study_id, exc)

    # ── results.tsv ────────────────────────────────────────────────

    @staticmethod
    def _update_results_tsv(
        runs_dir: Path,
        run_name: str,
        verdict: str,
        *,
        round_num: int | None = None,
        results_tsv: Path | None = None,
    ) -> None:
        """In-place verdict update with (round, run) composite matching.

        v2: run ids repeat per round (round_NNNN/run_0001…), so rows are
        matched by round (trailing column 13) + run name; legacy CLI rows
        (no round column) fall back to run-name matching.
        """
        results_path = results_tsv or (runs_dir / "results.tsv")
        if not results_path.exists():
            return
        content = results_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        for i in range(len(lines) - 1, 0, -1):
            parts = lines[i].split("\t")
            if len(parts) < 12:
                continue
            row_run = parts[0]
            row_round = parts[13] if len(parts) >= 14 else ""
            if row_run != run_name:
                continue
            if round_num is not None and row_round != str(round_num):
                continue
            if len(parts) >= 12:
                parts[11] = verdict
                lines[i] = "\t".join(parts)
            break
        results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── misc ───────────────────────────────────────────────────────

    def _round_cooldown(self) -> float:
        from strategy_research.core.autoresearch import get_cooldown_seconds
        return get_cooldown_seconds(
            self._get_study().cooldown_base * 2, self._get_study().cooldown_jitter * 2, self._get_study().min_cooldown * 2,
        )

    def _maybe_load_previous_summary(self, study: StudyRecord) -> dict | None:
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
            return load_run_summary(runs_dir / f"run_{max(nums):04d}")
        except Exception:
            return None

    def _mark_terminal(self, status: StudyStatus, *, last_metrics=None, last_error=None, reason=None):
        """Persist a terminal status to the DB (best-effort, H2).

        A transient DB failure must not crash the whole run — the caller
        still emits the SSE notification and the poll loop reconciles
        the UI from the DB on the next tick. Logged loudly for ops.
        """
        err = last_error if reason is None else f"{reason}:{last_error or ''}"
        try:
            self.study_store.update_execution_status(
                self._get_study().study_id, status,
                last_error=err, last_metrics=last_metrics,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort persistence
            logger.warning(
                "mark_terminal failed to persist %s for study %s: %s",
                status.value, self.study_id, exc,
            )

    async def _wait_until_resumed(self):
        while self.control.paused and not self.control.cancelled:
            await asyncio.sleep(0.5)

    def _emit(self, session_id: str, event: str, data: dict) -> None:
        # v4 observability: attach the live trace context (trace_id /
        # study_id / round_num) to every SSE event so the UI can show a
        # copyable trace_id for log correlation.
        try:
            from ..observability import get_trace_context
            ctx = get_trace_context()
            data = {
                "trace_id": ctx.get("trace_id"),
                "study_id": ctx.get("study_id") or data.get("study_id"),
                "round_num": ctx.get("round_num") or data.get("round"),
                **data,
            }
        except Exception:  # noqa: BLE001 — best-effort decoration
            pass
        try:
            self.emitter.emit(session_id, event, data)
        except Exception as exc:
            logger.debug("runner emit %s failed: %s", event, exc)

    def _open_goal_store(self):
        from strategy_research.core.goal import GoalStore
        return GoalStore()

    @staticmethod
    def _format_directives(directives) -> str:
        lines = ["<user-directives>", "Honour them in this round's research plan:"]
        for d in directives:
            lines.append(f"- [{d.created_at}] {d.content.replace(chr(10), ' ').strip()}")
        lines.append("</user-directives>")
        return "\n".join(lines)

    def _run_monitor_check(self) -> dict:
        """Re-backtest the last keep run and check if metrics still meet targets.

        Called by the monitor phase. Returns a dict with:
        - meets_targets: bool
        - metrics: dict
        - now_iso: str
        - verdict: "monitor"
        """
        from datetime import datetime, timezone
        from strategy_research.core.study import state_store as ss
        from strategy_research.core.autoresearch import read_current_state
        from strategy_research.core.study.metric_targets import meets_metric_targets

        study = self._get_study()
        sid = study.study_id
        path = Path(study.workspace_path).resolve()
        state = ss.load(path, sid)

        # Read current metrics from state.json (already updated by last keep round)
        metrics = state.best_metrics or {}

        # Check if metrics meet targets
        meets = bool(
            study.metric_targets
            and meets_metric_targets(metrics, study.metric_targets)
        )

        return {
            "meets_targets": meets,
            "metrics": metrics,
            "now_iso": datetime.now(timezone.utc).isoformat(),
            "verdict": "monitor",
        }


# ── Backward-compat alias ──────────────────────────────────────────
AutoresearchExecutor = AutoresearchRunner
