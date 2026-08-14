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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import StudyRecord, StudyStatus
from .store import StudyStore

logger = logging.getLogger(__name__)

# v2 review-cycle tuning (design §10/§11)
SR_STUDY_MAX_DEVIATION = 3          # consecutive high deviations → stop
SR_STUDY_COLLECT_INTERVAL = 5       # force info collection every K rounds
SR_STUDY_MAX_DISCARD = 5            # consecutive discards → stagnation stop


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


def meets_metric_targets(metrics: dict[str, Any], targets: list[dict]) -> bool:
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
            a, v = float(actual), float(value)
        except (TypeError, ValueError):
            return False
        if op == ">=":
            if not ((a >= v)):
                return False
        elif op == "<=":
            if not ((a <= v)):
                return False
        elif op == ">":
            if not ((a > v)):
                return False
        elif op == "<":
            if not ((a < v)):
                return False
        elif op == "==":
            if not ((a == v)):
                return False
        else:
            # unknown operator — treat as not-met
            return False
    return True


def _metric_pass_set(metrics: dict, targets: list[dict]) -> set[str]:
    """Return set of metric names that meet their targets."""
    passed = set()
    for t in targets:
        name = t.get("name")
        op = t.get("op", ">=")
        value = t.get("value")
        if name is None or value is None:
            continue
        actual = metrics.get(name)
        if actual is None:
            continue
        try:
            a, v = float(actual), float(value)
        except (TypeError, ValueError):
            continue
        if ((op == ">=" and a >= v) or (op == "<=" and a <= v)
                or (op == ">" and a > v) or (op == "<" and a < v)
                or (op == "==" and a == v)):
            passed.add(name)
    return passed


def acceptance_config_from_targets(
    targets: list[dict] | None,
) -> Any:
    """Map ``metric_targets`` → an ``AcceptanceConfig`` override.

    Called lazily (imports strategy_acceptance) so importing
    ``core.study.runner`` does not eagerly load the acceptance module —
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
        self.study_store = store
        self.control = control or ControlToken()
        self.emitter = emitter or NullEmitter()
        self._own_goal_store = goal_store is None
        self._goal_store = goal_store or self._open_goal_store()
        # AEGIS state
        self._prev_passed: set[str] = set()
        self._best_score: float = 0.0
        self._idle_rounds: int = 0
        # Budget accumulators
        self._round_start_clock: float | None = None
        self._total_used_time: float = 0.0
        self._total_used_turns: int = 0

    # ── public entrypoint ───────────────────────────────────────────

    async def run(self) -> str:
        sid = self.study.study_id
        session = self.study.session_id
        _dlog("runner", "run() starting study=%s session=%s max_rounds=%s",
              sid, session, self.study.max_rounds)
        self._emit(session, "study_started", {"study_id": sid, "round": self.study.current_round})
        reason = ShutdownReason.ERROR
        try:
            # v2 §15.2 recover: a MONITORING study restarts directly into
            # the monitor phase (no research rounds).
            if self.study.execution_status == StudyStatus.MONITORING:
                reason = await self._monitor_phase()
                return reason
            reason = await self._run_loop()
            # v2 §15.2: on E2 completion with monitoring enabled, enter the
            # post-completion monitor phase (rounds stop; periodic re-checks).
            # Runs in-sequence so the scheduler's control token and semaphore
            # stay alive for pause/resume/cancel during monitoring.
            if reason == ShutdownReason.TARGETS_MET and (
                self.study.monitor_interval_seconds or 0
            ) > 0:
                reason = await self._monitor_phase()
        except Exception as exc:
            _dlog("runner", "run() FAILED: study=%s error=%s", sid, exc)
            logger.exception("study %s failed: %s", sid, exc)
            self.study_store.update_execution_status(sid, StudyStatus.ERROR, last_error=f"{exc}"[:500])
            self._emit(session, "study_failed", {"study_id": sid, "error": f"{exc}"[:500], "reason": ShutdownReason.ERROR})
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
                "study_id": sid, "goal_id": self.study.goal_id,
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
        _st = _ss.load(Path(self.study.workspace_path), sid)
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
        sid = self.study.study_id
        session = self.study.session_id

        # Load previous summary for cross-round context
        previous_summary = self._maybe_load_previous_summary(self.study)
        # v2: best score comes from state.json (keep-only, design §8.4)
        from strategy_research.core.study import state_store as ss
        state = ss.load(Path(self.study.workspace_path), sid)
        best_calmar = (state.best_metrics or {}).get("calmar")
        if best_calmar is not None:
            self._best_score = float(best_calmar)
        elif previous_summary and previous_summary.get("metrics"):
            self._best_score = previous_summary["metrics"].get("calmar", 0.0)

        round_num = self.study.current_round

        while True:
            if self.control.cancelled:
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

            # Consume directives
            pending = self.study_store.list_pending_directives(sid)
            directive_text = self._format_directives(pending) if pending else None

            _dlog("loop", "round %d start study=%s", round_num, sid)

            # ── Run one round (overridable for tests) ──────────────
            result = await asyncio.to_thread(
                self._run_one_round, round_num, previous_summary, directive_text,
            )

            # Mark directives consumed
            if pending:
                self.study_store.mark_directives_consumed(sid, [d.directive_id for d in pending])

            # Handle aborted round (novelty rejected)
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

            # ── shutdown conditions (targets/budget/stagnation/review/discard) ──
            stop_reason = self._check_stop_conditions(
                result, metrics, verdict, round_num, session, sid
            )
            if stop_reason is not None:
                return stop_reason

            # ── shutdown: max_rounds ───────────────────────────────
            if self.study.max_rounds is not None and round_num >= self.study.max_rounds:
                self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                    last_error=f"max_rounds={self.study.max_rounds}", reason=ShutdownReason.MAX_ROUNDS)
                return ShutdownReason.MAX_ROUNDS

            # ── AEGIS: Early-stop (only when max_rounds is configured) ──
            if self.study.max_rounds is not None:
                current_score = metrics.get("calmar", 0.0) or 0.0
                if current_score > self._best_score:
                    self._best_score = current_score
                    self._idle_rounds = 0
                else:
                    self._idle_rounds += 1
                if self._idle_rounds >= 3:
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
        """Post-completion monitoring: periodic re-check + auto repair.

        Every ``monitor_interval_seconds`` the last keep run is re-backtested
        (no LLM) and compared to ``metric_targets`` only (gates are
        research-phase constraints, §15.2). Drift → NEEDS_REFRESH + up to
        3 full repair rounds; success returns to MONITORING, failure stays
        needs_refresh. Cancelled/paused handled via the shared control token.
        """
        sid = self.study.study_id
        session = self.study.session_id
        interval = self.study.monitor_interval_seconds or 0
        self.study_store.update_execution_status(sid, StudyStatus.MONITORING)
        self._emit(session, "study_monitoring_started", {
            "study_id": sid, "interval_seconds": interval,
        })
        _dlog("monitor", "monitoring started study=%s interval=%ss", sid, interval)
        while True:
            if self.control.cancelled:
                self._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
                self._emit(session, "study_cancelled", {"study_id": sid})
                return ShutdownReason.CANCELLED
            if self.control.paused:
                self.study_store.update_execution_status(sid, StudyStatus.PAUSED)
                self._emit(session, "study_paused", {
                    "study_id": sid, "round": self.study.current_round,
                })
                await self._wait_until_resumed()
                self.study_store.update_execution_status(sid, StudyStatus.MONITORING)
                self._emit(session, "study_resumed", {
                    "study_id": sid, "round": self.study.current_round,
                })

            await self._monitor_sleep(interval)
            try:
                check = await asyncio.to_thread(self._run_monitor_check)
            except Exception as exc:  # noqa: BLE001
                logger.warning("monitor check %s failed: %s", sid, exc)
                self._emit(session, "study_monitor_check_failed", {
                    "study_id": sid, "error": str(exc),
                })
                continue

            drift = not check["meets_targets"]
            self.study_store.update_monitor_check(
                sid, last_check_at=check["now_iso"], drift=drift,
            )
            self.study_store.update_last_metrics(
                sid, check["metrics"] or {}, check.get("verdict", "monitor"),
            )
            self._emit(session, "study_monitor_check", {
                "study_id": sid,
                "metrics": check["metrics"],
                "meets_targets": check["meets_targets"],
                "drift": drift,
                "drift_count": self.study.monitor_drift_count + (1 if drift else 0),
            })
            if not drift:
                continue

            # ── drift → needs_refresh + auto repair rounds ─────────
            self.study_store.update_execution_status(
                sid, StudyStatus.NEEDS_REFRESH,
                last_error=f"monitor drift: {check['reason']}",
            )
            self._emit(session, "study_drift_detected", {
                "study_id": sid, "metrics": check["metrics"],
                "reason": check["reason"],
            })
            if await self._monitor_repair_rounds():
                self.study_store.update_execution_status(sid, StudyStatus.MONITORING)
                self._emit(session, "study_monitoring_started", {
                    "study_id": sid, "interval_seconds": interval,
                    "repaired": True,
                })
                continue
            _dlog("monitor", "repair exhausted, staying needs_refresh study=%s", sid)
            return ShutdownReason.NEEDS_REFRESH

    async def _monitor_repair_rounds(self) -> bool:
        """Up to 3 full repair rounds; True when an E2 pass restores the study."""
        sid = self.study.study_id
        session = self.study.session_id
        path = Path(self.study.workspace_path)
        from strategy_research.core.study import state_store as ss
        state = ss.load(path, sid)
        base_round = state.last_completed_round or self.study.current_round or 0
        for attempt in range(3):
            if self._budget_exceeded():
                self.study_store.update_execution_status(
                    sid, StudyStatus.NEEDS_REFRESH,
                    last_error=f"budget_limited: {self._budget_summary()}",
                )
                self._emit(session, "study_budget_limited", {
                    "study_id": sid, "used": self._budget_summary(),
                })
                return False
            round_num = base_round + attempt + 1
            _dlog("monitor", "repair round %d study=%s", round_num, sid)
            self._round_start_clock = time.perf_counter()
            previous_summary = self._maybe_load_previous_summary(self.study)
            result = await asyncio.to_thread(
                self._run_one_round, round_num, previous_summary, None,
            )
            self._account_round_budget(result)
            if result.get("aborted"):
                continue
            metrics = result.get("metrics", {})
            verdict = result.get("verdict", "discard")
            self.study_store.update_round_heartbeat(sid, round_num)
            self.study_store.update_last_metrics(sid, metrics, verdict)
            self._emit(session, "study_round", {
                "study_id": sid, "round": round_num,
                "run": result.get("run_name", ""),
                "metrics": metrics, "verdict": verdict,
            })
            if result.get("e2_passed"):
                _dlog("monitor", "repair round %d passed, back to MONITORING", round_num)
                return True
        return False

    def _run_monitor_check(self) -> dict:
        """Single monitor check: re-backtest the last keep run, compare to
        ``metric_targets`` (no LLM, no gates). """
        from datetime import datetime, timezone

        from strategy_research.core.backtest import run_backtest_script
        from strategy_research.core.study import state_store as ss

        sid = self.study.study_id
        path = Path(self.study.workspace_path).resolve()
        now_iso = datetime.now(timezone.utc).isoformat()
        root = ss.study_root(path, sid)
        state = ss.load(path, sid)
        strategy_dir = None
        if state.last_keep_run_dir:
            candidate = root / state.last_keep_run_dir
            if (candidate / "strategy.py").exists():
                strategy_dir = candidate
        result = run_backtest_script(
            workspace_path=path,
            strategy_name=self.study.strategy_name,
            action="monitor",
            description="post-completion monitoring re-check",
            run_dir=root / "monitor",
            strategy_dir=strategy_dir,
            results_tsv=root / "results.tsv",
        )
        if not result.get("success"):
            # backtest failure surfaces as drift
            return {
                "metrics": {},
                "verdict": "error",
                "meets_targets": False,
                "reason": f"backtest failed: {result.get('error', 'unknown')}",
                "now_iso": now_iso,
            }
        metrics = result.get("metrics", {}) or {}
        ok = not self.study.metric_targets or meets_metric_targets(
            metrics, self.study.metric_targets,
        )
        return {
            "metrics": metrics,
            "verdict": "monitor",
            "meets_targets": ok,
            "reason": "" if ok else "metric_targets no longer met",
            "now_iso": now_iso,
        }

    async def _monitor_sleep(self, interval: float) -> None:
        """Sleep between monitor checks. Tests override this to skip the wait."""
        if interval > 0:
            await asyncio.sleep(interval)

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
        from ..observability import bind_trace

        with bind_trace(
            study_id=self.study.study_id,
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
        """Actual round implementation (called inside ``bind_trace``)."""
        from strategy_research.core.autoresearch import (
            _create_run_dir,
            generate_run_summary,
            read_current_state,
            run_evaluation_phase,
            run_execution_phase,
            run_researcher_phase,
            save_run_summary,
        )
        from strategy_research.core.study import review_loop as rl
        from strategy_research.core.study import round_manifest as rm
        from strategy_research.core.study import state_store as ss

        sid = self.study.study_id
        session = self.study.session_id
        path = Path(self.study.workspace_path).resolve()
        strategy = self.study.strategy_name
        metric_targets = self.study.metric_targets
        root = ss.study_root(path, sid)
        state = ss.load(path, sid)

        # ── v2 round-start knowledge gap check (design §11.1) ─────
        knowledge_path = root / "knowledge.md"
        knowledge_text = (
            knowledge_path.read_text(encoding="utf-8")
            if knowledge_path.exists() else ""
        )
        next_focus = ""
        if state.last_review:
            next_focus = str(state.last_review.get("next_focus") or "")
        gap_topics = rl.gap_check(
            self.study.objective, next_focus, knowledge_text,
        )
        if gap_topics:
            self._collect_knowledge(gap_topics)
        self._emit(session, "study_knowledge_check", {
            "study_id": sid, "round": round_num,
            "gap_topics": gap_topics, "collected": bool(gap_topics),
        })

        # ── round dir + inherited strategy copy ─────────────────────
        round_dir = rm.round_dir(path, sid, round_num)
        round_dir.mkdir(parents=True, exist_ok=True)

        runs_dir, run_name, run_dir = _create_run_dir(
            path, strategy, runs_dir=round_dir,
        )
        # inheritance: last keep run (or baseline) → this round's strategy
        adopted = rm.resolve_adopted_run_for_start(state.last_keep_run_dir)
        inherited_from = adopted
        src_strategy = (root / adopted / "strategy.py") if adopted != "baseline" \
            else root / "baseline" / "strategy.py"
        dst_strategy = run_dir / "strategy.py"
        if src_strategy.exists():
            dst_strategy.write_text(
                src_strategy.read_text(encoding="utf-8"), encoding="utf-8",
            )
        src_cfg = root / adopted / "config.yaml" if adopted != "baseline" \
            else root / "baseline" / "config.yaml"
        if src_cfg.exists():
            (run_dir / "config.yaml").write_text(
                src_cfg.read_text(encoding="utf-8"), encoding="utf-8",
            )

        results_tsv = root / "results.tsv"

        # read state (study layout: strategy in run dir, tsv at study root)
        current_state = read_current_state(
            path, strategy,
            strategy_file=dst_strategy,
            results_tsv=results_tsv,
        )
        current_state["study_strategy_path"] = str(
            round_dir.relative_to(path) / run_name / "strategy.py"
        )

        # AEGIS: inject journal + scoreboard context
        journal_ctx = self._build_journal_context()
        scoreboard_ctx = self._build_scoreboard_context()
        if journal_ctx:
            current_state["journal_context"] = journal_ctx
        if scoreboard_ctx:
            current_state["lever_scoreboard"] = scoreboard_ctx

        # v2 guidance: human decision points injected every round (§13.2)
        from strategy_research.core.study import guidance as gd
        guidance = gd.load_guidance(path, sid)
        guidance_section = gd.render_guidance_section(guidance)
        if guidance_section:
            current_state["human_guidance"] = guidance_section

        # Inject factor failures from previous round
        if previous_summary and previous_summary.get("factor_failures"):
            current_state["factor_failures"] = previous_summary["factor_failures"]

        # Phase 1: researcher
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "researcher", "status": "started",
        })
        researcher_result = run_researcher_phase(
            path, strategy, current_state, run_dir,
            session_id=session, run_name=run_name,
            behavior=self.study.behavior, max_retries=3, max_iterations=10,
            directives=directive_text,
            lazy_detection_interval=self.study.lazy_detection_interval,
            keep_recent=self.study.keep_recent, round_num=round_num,
            runs_dir=runs_dir,
        )
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "researcher", "status": "done",
        })
        researcher_output = researcher_result["researcher_output"]

        # AEGIS: Novelty Gate
        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected") or [t["name"] for t in metric_targets]
        if not self._novelty_gate(round_num, hypothesis, predicted_affected):
            return {"round": round_num, "run_name": run_name, "aborted": True,
                    "reason": "novelty_rejected"}

        # Phase 2: execution
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "execution", "status": "started",
        })
        exec_result = run_execution_phase(
            path, strategy, current_state, researcher_output, run_dir,
            session_id=session, run_name=run_name,
            behavior=self.study.behavior, max_retries=3, max_iterations=10,
            strategy_dir=run_dir,
            results_tsv=results_tsv,
            round_num=round_num,
        )
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "execution", "status": "done",
        })
        metrics = exec_result["metrics"]
        strategist_output = exec_result["strategist_output"]

        # Phase 3: evaluation
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "evaluation", "status": "started",
        })
        eval_result = run_evaluation_phase(
            path, strategy, exec_result["backtest_result"], metrics, run_dir,
            behavior=self.study.behavior, max_retries=3, max_iterations=10,
        )
        self._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "evaluation", "status": "done",
        })
        verdict = eval_result["verdict"]

        # ── guidance gates hard check (design §13.3): before verdict ──
        gate_violations = self._check_guidance_gates(guidance, metrics)
        if gate_violations:
            verdict = "discard"

        # E2 completion semantics (§15.2): targets ∧ keep ∧ gates pass
        e2_passed = bool(
            self.study.metric_targets
            and meets_metric_targets(metrics, self.study.metric_targets)
            and verdict == "keep"
            and not gate_violations
        )

        # disk: results.tsv (round column) + summary
        self._update_results_tsv(
            runs_dir, run_name, verdict,
            round_num=round_num, results_tsv=results_tsv,
        )
        agent_outputs = {
            "researcher": researcher_output,
            **{k: exec_result.get(k) for k in (
                "data_quality_output", "factor_analyst_output",
                "strategist_output", "portfolio_construction_output",
            )},
            **{k: eval_result.get(k) for k in (
                "risk_controller_output", "attribution_analyst_output",
                "anti_overfit_analyst_output", "backtest_diagnostics_output",
            )},
        }
        summary = generate_run_summary(agent_outputs, metrics, verdict, round_num, previous_summary)
        summary["acceptance_decision"] = eval_result["decision"].to_dict()
        save_run_summary(run_dir, summary)

        # AEGIS: Attribution + Journal + Regression Gate
        passed_now = _metric_pass_set(metrics, metric_targets)
        from .attribution import classify_attribution
        attribution = classify_attribution(predicted_affected, self._prev_passed, passed_now)

        lever = strategist_output.get("action", "unknown") if isinstance(strategist_output, dict) else "unknown"
        gating_outcome = "accepted" if verdict == "keep" else "reverted"
        self._record_journal_and_regression(
            round_num, hypothesis, predicted_affected, lever,
            strategist_output, gating_outcome, attribution,
        )

        # AEGIS: Scoreboard
        from ..goal.scoreboard import LeverScoreboard
        if not hasattr(self, "_scoreboard"):
            self._scoreboard = LeverScoreboard()
        self._scoreboard.update([lever], attribution, gating_outcome, round_num, round_num)

        # ── v2 artifacts (phase 1): manifest + summary.md + journal.md ──
        verdict_reason = self._verdict_reason(eval_result, strategist_output)
        if gate_violations:
            gate_reason = "guidance gates: " + ",".join(v["id"] for v in gate_violations)
            verdict_reason = f"{verdict_reason} | {gate_reason}" if verdict_reason else gate_reason
        strategy_changes = (
            strategist_output.get("changes")
            if isinstance(strategist_output, dict) else None
        )
        manifest = rm.build_manifest(
            round_num=round_num,
            inherited_from=inherited_from,
            adopted_run=state.last_keep_run_dir,
            run_name=run_name,
            hypothesis=hypothesis,
            levers=[lever],
            predicted_affected=predicted_affected,
            strategy_changes=strategy_changes,
            metrics=metrics,
            prev_metrics=summary.get("performance_change") or None,
            baseline_metrics=state.baseline_best or None,
            verdict=verdict,
            verdict_reason=verdict_reason,
            gates=gate_violations or None,
            budget={
                "turns_used": self._total_used_turns,
                "time_used_s": round(self._total_used_time, 1),
                "total": {
                    "turns": self.study.budget_turn,
                    "time_s": self.study.budget_time_seconds,
                },
            },
        )
        rm.save_manifest(manifest, path, sid, round_num)
        summary_md = rm.render_round_markdown(manifest, self.study.objective)
        (rm.summary_path(path, sid, round_num)).write_text(
            summary_md, encoding="utf-8",
        )
        rm.append_journal_md(path, sid, manifest, self.study.objective)

        # ── state.json update (authority; DB mirrors later in _run_loop) ──
        self._update_round_state(
            path, sid, round_num, run_name, verdict, metrics, state, strategy_changes,
        )

        # ── goal ledger: keep-round evidence + criteria progress (E1) ──
        if verdict == "keep" and self.study.goal_id:
            self._record_keep_evidence(round_num, run_name, metrics)

        # ── v2 review cycle (phase 2: review + collect + todos) ─────
        review_stop = self._run_review_cycle(
            round_num, manifest, state, verdict, hypothesis,
        )

        return {
            "round": round_num, "run_name": run_name, "run_dir": run_dir,
            "metrics": metrics, "verdict": verdict,
            "decision": eval_result["decision"].to_dict(),
            "agent_outputs": agent_outputs, "summary": summary,
            "backtest_error": exec_result.get("backtest_error"),
            "passed_now": passed_now,
            "manifest": manifest,
            "state": state,
            "review_stop": review_stop,
            "e2_passed": e2_passed,
        }

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
        self._emit(self.study.session_id, "study_round_rejected", {
            "study_id": self.study.study_id, "round": round_num,
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
        session = self.study.session_id
        self._goal_store.append_journal_entry(
            self.study.goal_id, session, round_num, hypothesis, hypothesis[:60],
            levers=[lever], predicted_affected=predicted_affected,
            changeset=strategist_output.get("changes") if isinstance(strategist_output, dict) else None,
        )
        self._goal_store.fill_journal_attribution(
            self.study.goal_id, session, round_num, gating_outcome, attribution,
        )
        passes, regressed = self._check_regression(attribution)
        if not passes:
            self._archive_rejected(round_num, hypothesis, "regression", str(regressed))
            self._emit(session, "study_round_rejected", {
                "study_id": self.study.study_id, "round": round_num,
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

        # DB mirror: study_rounds row (phase 1 body)
        try:
            self.study_store.append_round(
                sid, round_num, run_name,
                metrics=metrics, verdict=verdict,
                config_changes=strategy_changes,
            )
        except Exception as exc:  # noqa: BLE001 — file-first; DB is mirror
            logger.warning("append_round failed (mirror): %s", exc)

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

        sid = self.study.study_id
        session = self.study.session_id
        path = Path(self.study.workspace_path).resolve()
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
            f"objective: {self.study.objective}\n"
            f"metric_targets: {json.dumps(self.study.metric_targets, ensure_ascii=False)}\n"
            f"round: {round_num}\n"
            f"verdict: {verdict}\n"
            f"hypothesis: {hypothesis}\n"
            f"manifest: {json.dumps(manifest, ensure_ascii=False, default=str)[:4000]}\n"
            f"last_review: {json.dumps(state.last_review, ensure_ascii=False, default=str)}\n"
            f"continuous_deviation: {state.continuous_deviation}\n"
            f"todos:\n{todos_path.read_text(encoding='utf-8') if todos_path.exists() else ''}\n"
            f"knowledge (recent):\n{knowledge_text[-3000:]}\n"
        )
        use_real = self.study.behavior is None and should_use_real_llm()
        raw_review = ""
        try:
            if use_real:
                raw_review = run_agent_via_llm(
                    role="study_reviewer",
                    workspace_path=path,
                    strategy_name=self.study.strategy_name,
                    task=review_input,
                    max_iterations=3,
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
            topics = review["topics"] or [self.study.objective[:80]]
            self._collect_knowledge(topics)

        # ── ③ todos application ────────────────────────────────────
        applied = rl.apply_todos(
            todos_path, review["todo_updates"], self.study.objective,
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

        sid = self.study.study_id
        path = Path(self.study.workspace_path).resolve()
        root = ss.study_root(path, sid)
        knowledge_path = root / "knowledge.md"
        knowledge_text = (
            knowledge_path.read_text(encoding="utf-8")
            if knowledge_path.exists() else ""
        )
        try:
            if self.study.behavior is None and should_use_real_llm():
                raw_entries = run_agent_via_llm(
                    role="study_collector",
                    workspace_path=path,
                    strategy_name=self.study.strategy_name,
                    task=(
                        f"objective: {self.study.objective}\n"
                        f"topics: {json.dumps(topics, ensure_ascii=False)}\n"
                        f"existing knowledge:\n{knowledge_text[-2000:]}\n"
                    ),
                    max_iterations=3,
                )
                entries = rl.parse_review_output(raw_entries)
                if isinstance(entries, dict):
                    entries = [entries]
            else:
                entries = []
            collected = rl.append_knowledge(
                knowledge_path, entries, self.study.objective,
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
        sid = self.study.study_id
        session = self.study.session_id
        try:
            from strategy_research.core.goal import EvidenceInput
            ev = self._goal_store.append_evidence(
                session_id=session,
                goal_id=self.study.goal_id,
                expected_goal_id=self.study.goal_id,
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
            criteria = self._goal_store.list_criteria(self.study.goal_id)
            evidence = self._goal_store.list_evidence(self.study.goal_id)
            seen = {e.criterion_id for e in evidence if e.criterion_id}
            covered = sum(1 for c in criteria if c.criterion_id in seen)
            total = len(criteria)
            self._emit(session, "study_progress", {
                "study_id": sid, "covered": covered, "total": total,
                "percent": round(covered / total * 100) if total else 100,
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
        if not self.study.goal_id:
            return True, None
        return self._goal_store.check_novelty(
            self.study.goal_id, hypothesis, [], predicted_affected,
        )

    def _check_regression(self, attribution: dict[str, str]) -> tuple[bool, list[str]]:
        if not self.study.goal_id:
            return True, []
        return self._goal_store.check_regression(self.study.goal_id, attribution)

    def _archive_rejected(self, round_num: int, hypothesis: str, reason: str, detail: str) -> None:
        if not self.study.goal_id:
            return
        self._goal_store.archive_rejected_edit(
            self.study.goal_id, round_num, hypothesis, reason, detail,
        )

    def _build_journal_context(self) -> str:
        if not self.study.goal_id:
            return ""
        return self._goal_store.build_journal_context(self.study.goal_id, self.study.current_round)

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
        if not self.study.goal_id:
            return
        try:
            from strategy_research.core.goal import EvidenceInput
            metrics = exec_result.get("metrics", {})
            run_name = exec_result.get("run_name", "")
            existing = self._goal_store.list_evidence(self.study.goal_id)
            seen = {ev.criterion_id for ev in existing if ev.criterion_id}
            criteria = self._goal_store.list_criteria(self.study.goal_id)
            for c in criteria:
                if not c.required or c.criterion_id in seen:
                    continue
                self._goal_store.append_evidence(
                    session_id=self.study.session_id,
                    goal_id=self.study.goal_id,
                    expected_goal_id=self.study.goal_id,
                    evidence=EvidenceInput(
                        text=f"Study 达标自动覆盖 — {run_name}: Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')} MaxDD={metrics.get('max_dd')}",
                        criterion_id=c.criterion_id, evidence_type="acceptance",
                        run_id=run_name, source_provider="study", source_type="metric_targets_met",
                    ),
                )
            self._goal_store.complete_lite(
                session_id=self.study.session_id, goal_id=self.study.goal_id,
                expected_goal_id=self.study.goal_id,
                recap=f"研究达标 — Calmar={metrics.get('calmar')}, Sharpe={metrics.get('sharpe')}, MaxDD={metrics.get('max_dd')}",
            )
        except Exception as exc:
            logger.exception("study %s goal completion failed: %s", self.study.study_id, exc)

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
            self.study.cooldown_base * 2, self.study.cooldown_jitter * 2, self.study.min_cooldown * 2,
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
        err = last_error if reason is None else f"{reason}:{last_error or ''}"
        self.study_store.update_execution_status(self.study.study_id, status, last_error=err, last_metrics=last_metrics)

    async def _wait_until_resumed(self):
        while self.control.paused and not self.control.cancelled:
            await asyncio.sleep(0.5)

    def _emit(self, session_id: str, event: str, data: dict) -> None:
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


# ── Backward-compat alias ──────────────────────────────────────────
AutoresearchExecutor = AutoresearchRunner
