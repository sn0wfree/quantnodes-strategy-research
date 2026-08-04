"""AutoresearchRunner — AEGIS-powered study executor.

Upgraded version of ``AutoresearchExecutor`` with built-in AEGIS mechanisms:
Novelty Gate, Attribution, Lever Scoreboard, Regression Gate, and Early-stop.

Uses the phase-split ``run_researcher_phase`` / ``run_execution_phase`` /
``run_evaluation_phase`` from ``autoresearch.py`` so AEGIS hooks can be
injected between phases.
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
            if not (a >= v): return False
        elif op == "<=":
            if not (a <= v): return False
        elif op == ">":
            if not (a > v): return False
        elif op == "<":
            if not (a < v): return False
        elif op == "==":
            if not (a == v): return False
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
            reason = await self._run_loop()
        except Exception as exc:
            _dlog("runner", "run() FAILED: study=%s error=%s", sid, exc)
            logger.exception("study %s failed: %s", sid, exc)
            self.study_store.update_execution_status(sid, StudyStatus.ERROR, last_error=f"{exc}"[:500])
            self._emit(session, "study_failed", {"study_id": sid, "error": f"{exc}"[:500], "reason": ShutdownReason.ERROR})
        finally:
            if self._own_goal_store:
                try: self._goal_store.close()
                except Exception: pass
            self._emit(session, "study_executor_stopped", {"study_id": sid, "reason": reason})
        return reason

    # ── main loop ──────────────────────────────────────────────────

    async def _run_loop(self) -> str:
        sid = self.study.study_id
        session = self.study.session_id

        # Load previous summary for cross-round context
        previous_summary = self._maybe_load_previous_summary(self.study)
        if previous_summary and previous_summary.get("metrics"):
            self._best_score = previous_summary["metrics"].get("calmar", 0.0)

        round_num = self.study.current_round

        while True:
            if self.control.cancelled:
                self._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
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

            # ── SSE: study_round ───────────────────────────────────
            self._emit(session, "study_round", {
                "study_id": sid, "round": round_num,
                "run": result.get("run_name", ""),
                "metrics": metrics, "verdict": verdict,
            })

            # ── shutdown: targets met ──────────────────────────────
            if self.study.metric_targets and meets_metric_targets(metrics, self.study.metric_targets):
                self._complete_goal(result)
                self._mark_terminal(StudyStatus.COMPLETE, last_metrics=metrics, reason=ShutdownReason.TARGETS_MET)
                self._emit(session, "study_completed", {
                    "study_id": sid, "goal_id": self.study.goal_id,
                    "metrics": metrics, "round": round_num, "recap": verdict,
                })
                return ShutdownReason.TARGETS_MET

            # ── shutdown: budget ───────────────────────────────────
            if self._budget_exceeded():
                self._mark_terminal(StudyStatus.BUDGET_LIMITED, last_metrics=metrics,
                                    last_error=self._budget_summary(), reason=ShutdownReason.BUDGET)
                return ShutdownReason.BUDGET

            # ── shutdown: stagnation ───────────────────────────────
            decision = result.get("decision")
            if decision and isinstance(decision, dict) and decision.get("stagnation_triggered"):
                self._mark_terminal(StudyStatus.ERROR, last_metrics=metrics,
                                    last_error="stagnation", reason=ShutdownReason.STAGNATION)
                return ShutdownReason.STAGNATION

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

    def _run_one_round(
        self,
        round_num: int,
        previous_summary: dict | None,
        directive_text: str | None,
    ) -> dict:
        """Execute one round: phases + AEGIS hooks.

        Overridable for tests to stub round execution.
        """
        from strategy_research.core.autoresearch import (
            read_current_state, generate_run_summary, save_run_summary,
            _create_run_dir,
        )
        from strategy_research.core.autoresearch import (
            run_researcher_phase, run_execution_phase, run_evaluation_phase,
        )

        sid = self.study.study_id
        session = self.study.session_id
        path = Path(self.study.workspace_path).resolve()
        strategy = self.study.strategy_name
        metric_targets = self.study.metric_targets

        # read state + create run dir
        current_state = read_current_state(path, strategy)
        runs_dir, run_name, run_dir = _create_run_dir(path, strategy)

        # AEGIS: inject journal + scoreboard context
        journal_ctx = self._build_journal_context()
        scoreboard_ctx = self._build_scoreboard_context()
        if journal_ctx:
            current_state["journal_context"] = journal_ctx
        if scoreboard_ctx:
            current_state["lever_scoreboard"] = scoreboard_ctx

        # Inject factor failures from previous round
        if previous_summary and previous_summary.get("factor_failures"):
            current_state["factor_failures"] = previous_summary["factor_failures"]

        # Phase 1: researcher
        researcher_result = run_researcher_phase(
            path, strategy, current_state, run_dir,
            session_id=session, run_name=run_name,
            behavior=self.study.behavior, max_retries=3,
            directives=directive_text,
            lazy_detection_interval=self.study.lazy_detection_interval,
            keep_recent=self.study.keep_recent, round_num=round_num,
        )
        researcher_output = researcher_result["researcher_output"]

        # AEGIS: Novelty Gate
        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected") or [t["name"] for t in metric_targets]
        is_novel, novelty_reason = self._check_novelty(hypothesis, predicted_affected)
        if not is_novel:
            self._archive_rejected(round_num, hypothesis, "novelty", novelty_reason)
            self._emit(session, "study_round_rejected", {
                "study_id": sid, "round": round_num, "reason": "novelty", "detail": novelty_reason,
            })
            return {"round": round_num, "run_name": run_name, "aborted": True,
                    "reason": "novelty_rejected"}

        # Phase 2: execution
        exec_result = run_execution_phase(
            path, strategy, current_state, researcher_output, run_dir,
            session_id=session, run_name=run_name,
            behavior=self.study.behavior, max_retries=3,
        )
        metrics = exec_result["metrics"]
        strategist_output = exec_result["strategist_output"]

        # Phase 3: evaluation
        eval_result = run_evaluation_phase(
            path, strategy, exec_result["backtest_result"], metrics, run_dir,
            behavior=self.study.behavior, max_retries=3,
        )
        verdict = eval_result["verdict"]

        # disk: results.tsv + summary
        self._update_results_tsv(runs_dir, run_name, verdict)
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

        # AEGIS: Attribution
        passed_now = _metric_pass_set(metrics, metric_targets)
        from .attribution import classify_attribution
        attribution = classify_attribution(predicted_affected, self._prev_passed, passed_now)

        # AEGIS: Journal + Regression Gate
        lever = strategist_output.get("action", "unknown") if isinstance(strategist_output, dict) else "unknown"
        gating_outcome = "accepted" if verdict == "keep" else "reverted"
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
                "study_id": sid, "round": round_num, "reason": "regression", "regressed": regressed,
            })

        # AEGIS: Scoreboard
        from ..goal.scoreboard import LeverScoreboard
        if not hasattr(self, "_scoreboard"):
            self._scoreboard = LeverScoreboard()
        self._scoreboard.update([lever], attribution, gating_outcome, round_num, round_num)

        return {
            "round": round_num, "run_name": run_name, "run_dir": run_dir,
            "metrics": metrics, "verdict": verdict,
            "decision": eval_result["decision"].to_dict(),
            "agent_outputs": agent_outputs, "summary": summary,
            "backtest_error": exec_result.get("backtest_error"),
            "passed_now": passed_now,
        }

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
    def _update_results_tsv(runs_dir: Path, run_name: str, verdict: str) -> None:
        results_path = runs_dir / "results.tsv"
        if not results_path.exists():
            return
        content = results_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].startswith(run_name + "\t") or lines[i].startswith(run_name + " "):
                parts = lines[i].split("\t")
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
                    try: nums.append(int(d.name.split("_")[1]))
                    except (ValueError, IndexError): pass
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
        try: self.emitter.emit(session_id, event, data)
        except Exception as exc: logger.debug("runner emit %s failed: %s", event, exc)

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
