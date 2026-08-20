"""Monitor phase — post-completion periodic re-check + auto repair.

Extracted from runner.py to reduce file size and improve modularity.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _dlog(module: str, msg: str, *args) -> None:
    msg_fmt = msg % args if args else msg
    logger.info("[RUNNER:%s] %s", module, msg_fmt)
    print(f"[RUNNER:{module}] {msg_fmt}", flush=True)


async def monitor_phase(runner: Any) -> str:
    """Post-completion monitoring: periodic re-check + auto repair.

    Every ``monitor_interval_seconds`` the last keep run is re-backtested
    (no LLM) and compared to ``metric_targets`` only.
    """
    from ..models import StudyStatus
    from .runner import ShutdownReason

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    interval = study.monitor_interval_seconds or 0
    runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
    runner._emit(session, "study_monitoring_started", {
        "study_id": sid, "interval_seconds": interval,
    })
    _dlog("monitor", "monitoring started study=%s interval=%ss", sid, interval)

    while True:
        # Check cancelled
        if runner.control.cancelled:
            live_status = runner._current_db_status()
            if live_status == StudyStatus.ARCHIVED:
                runner._emit(session, "study_cancelled", {
                    "study_id": sid,
                    "note": f"preserved live status={live_status.value}",
                })
                return ShutdownReason.CANCELLED
            runner._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
            runner._emit(session, "study_cancelled", {"study_id": sid})
            return ShutdownReason.CANCELLED

        # Check paused
        if runner.control.paused:
            runner.study_store.update_execution_status(sid, StudyStatus.PAUSED)
            runner._emit(session, "study_paused", {
                "study_id": sid, "round": runner._get_study().current_round,
            })
            await runner._wait_until_resumed()
            runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
            runner._emit(session, "study_resumed", {
                "study_id": sid, "round": runner._get_study().current_round,
            })

        # Sleep and check
        await _monitor_sleep(interval)
        try:
            check = await asyncio.to_thread(runner._run_monitor_check)
        except Exception as exc:
            logger.warning("monitor check %s failed: %s", sid, exc)
            runner._emit(session, "study_monitor_check_failed", {
                "study_id": sid, "error": str(exc),
            })
            continue

        drift = not check["meets_targets"]
        runner.study_store.update_monitor_check(
            sid, last_check_at=check["now_iso"], drift=drift,
        )
        runner.study_store.update_last_metrics(
            sid, check["metrics"] or {}, check.get("verdict", "monitor"),
        )
        runner._emit(session, "study_monitor_check", {
            "study_id": sid,
            "metrics": check["metrics"],
            "meets_targets": check["meets_targets"],
            "drift": drift,
            "drift_count": runner._get_study().monitor_drift_count + (1 if drift else 0),
        })
        if not drift:
            continue

        # Drift → needs_refresh + auto repair
        runner.study_store.update_execution_status(
            sid, StudyStatus.NEEDS_REFRESH,
            last_error=f"monitor drift: {check['reason']}",
        )
        runner._emit(session, "study_drift_detected", {
            "study_id": sid, "metrics": check["metrics"],
            "reason": check["reason"],
        })
        if await _monitor_repair_rounds(runner):
            runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
            runner._emit(session, "study_monitoring_started", {
                "study_id": sid, "interval_seconds": interval,
                "repaired": True,
            })
            continue
        _dlog("monitor", "repair exhausted, staying needs_refresh study=%s", sid)
        return ShutdownReason.NEEDS_REFRESH


async def _monitor_repair_rounds(runner: Any) -> bool:
    """Up to 3 full repair rounds; True when an E2 pass restores the study."""
    from ..models import StudyStatus
    from ..budget import budget_exceeded, budget_summary, account_round_budget, maybe_load_previous_summary

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    path = Path(study.workspace_path)
    from strategy_research.core.study import state_store as ss
    state = ss.load(path, sid)
    base_round = state.last_completed_round or study.current_round or 0

    for attempt in range(3):
        if budget_exceeded(study, runner._total_used_time, runner._total_used_turns):
            runner.study_store.update_execution_status(
                sid, StudyStatus.NEEDS_REFRESH,
                last_error=f"budget_limited: {budget_summary(runner._total_used_turns, runner._total_used_time)}",
            )
            runner._emit(session, "study_budget_limited", {
                "study_id": sid, "used": budget_summary(runner._total_used_turns, runner._total_used_time),
            })
            return False

        round_num = base_round + attempt + 1
        _dlog("monitor", "repair round %d study=%s", round_num, sid)
        runner._round_start_clock = time.perf_counter()
        previous_summary = maybe_load_previous_summary(study)
        result = await asyncio.to_thread(
            runner._run_one_round, round_num, previous_summary, None,
        )
        runner._total_used_time, runner._total_used_turns = account_round_budget(
            runner._total_used_time, runner._total_used_turns,
            runner._round_start_clock, result,
        )
        if result.get("aborted"):
            continue
        metrics = result.get("metrics", {})
        verdict = result.get("verdict", "discard")
        runner.study_store.update_round_heartbeat(sid, round_num)
        runner.study_store.update_last_metrics(sid, metrics, verdict)
        runner._emit(session, "study_round", {
            "study_id": sid, "round": round_num,
            "run": result.get("run_name", ""),
            "metrics": metrics, "verdict": verdict,
        })
        if result.get("e2_passed"):
            _dlog("monitor", "repair round %d passed, back to MONITORING", round_num)
            return True
    return False


async def _monitor_sleep(interval: float) -> None:
    """Sleep between monitor checks."""
    if interval > 0:
        await asyncio.sleep(interval)
