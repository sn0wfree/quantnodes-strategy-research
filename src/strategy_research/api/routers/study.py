"""Study API router — ``/api/study/*``.

Exposes the study task system: create a study (which creates a goal
ledger row + queues the autoresearch executor), inspect status, and
pause / resume / cancel. See ``docs/study-longhorizon-plan.md``.

The scheduler is lazily wired to ``SessionService`` so the chat/study
mutex (``is_session_processing`` / ``mark_session_processing``) works
out of the box.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..schemas.study import (
    StudyActionItem,
    StudyActionRequest,
    StudyActionResponse,
    StudyAdoptResponse,
    StudyAvailableActionsResponse,
    StudyDirectiveCreatedResponse,
    StudyDirectivesResponse,
    StudyGraphBody,
    StudyGraphResponse,
    StudyGuidanceResponse,
    StudyHangingEventsResponse,
    StudyJournalResponse,
    StudyKnowledgeResponse,
    StudyListResponse,
    StudyObjectiveHistoryResponse,
    StudyRoundAgentOutputsResponse,
    StudyRoundArtifactsResponse,
    StudyRoundDiffResponse,
    StudyRoundManifestResponse,
    StudyRoundsResponse,
    StudyRoundSummaryMdResponse,
    StudyStartResponse,
    StudyStatusResponse,
    StudySummaryResponse,
    StudyTodosResponse,
)
from ._task_utils import log_task_exception

if TYPE_CHECKING:
    from ...core.study.scheduler import StudyScheduler

router = APIRouter()
logger = logging.getLogger(__name__)


# ── scheduler cache (mirrors chat._session_service_cache pattern) ───


_scheduler_cache: dict[str, "StudyScheduler"] = {}


def _get_study_scheduler():
    """Return the process-wide StudyScheduler for the study/goal DB.

    Builds it bound to the cached SessionService so the chat/study mutex
    primitives cooperate. Idempotent per DB path.

    IMPORTANT: the store must use the SAME database as every study
    creation site (``StudyStore()`` — the default goals.db path).
    Using ``_get_db_path()`` here (the session/EventStore DB) made the
    scheduler read from a different SQLite file than the one where
    ``/study start`` wrote the row — the consumer loop then saw no
    study and silently exited.
    """
    from ...core.study import StudyScheduler, StudyStore
    from .chat import _get_session_service

    db_path = str(StudyStore().db_path)
    sched = _scheduler_cache.get(db_path)
    if sched is None:
        store = StudyStore(db_path=db_path)
        svc = _get_session_service()
        sched = StudyScheduler(store, session_service=svc)
        _scheduler_cache[db_path] = sched
        logger.info("study scheduler instantiated for db=%s", db_path)
    return sched


# ── ops: in-process status dump (A.observability) ─────────────────


async def _study_dump(session_id: str, study_id: str | None) -> dict:
    """Build a JSON-safe status snapshot for an ops dump request.

    Pulls from the process-wide scheduler (active executors, watchdog,
    queue depth) and the SQLite store (study records). All times are
    ISO-8601 UTC; ``heartbeat_age_s`` is computed against the server's
    own monotonic clock so the operator can read the staleness at a
    glance.
    """
    from datetime import datetime, timezone

    from ...core.study.models import StudyStatus as _SS

    sched = _get_study_scheduler()
    now = datetime.now(timezone.utc).isoformat()

    studies_payload: list[dict] = []
    with sched.store as store:
        rows = store.list_studies(session_id=session_id, limit=200)
        if study_id:
            rows = [r for r in rows if r.study_id == study_id]
        for s in rows:
            hb_age: float | None = None
            hb_stale = False
            if s.heartbeat:
                try:
                    hb_dt = datetime.fromisoformat(s.heartbeat)
                    if hb_dt.tzinfo is None:
                        hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                    hb_age = max(0.0,
                                 (datetime.now(timezone.utc) - hb_dt).total_seconds())
                    hb_stale = hb_age > sched._heartbeat_timeout
                except ValueError:
                    pass
            in_scheduler = (
                s.study_id in sched._active_executors
                or s.study_id in sched._active_tasks
                or s.study_id in sched._dispatch_tasks
                or s.study_id in sched._control_tokens
            )
            studies_payload.append({
                "study_id": s.study_id,
                "objective": s.objective,
                "strategy_name": s.strategy_name,
                "execution_status": s.execution_status.value,
                "current_round": s.current_round,
                "max_rounds": s.max_rounds,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "heartbeat": s.heartbeat,
                "heartbeat_age_s": (round(hb_age, 1) if hb_age is not None else None),
                "last_error": s.last_error,
                "hanging_protection": {
                    "is_active_in_scheduler": in_scheduler,
                    "heartbeat_stale": hb_stale,
                    "watchdog_will_interrupt": (
                        hb_stale and in_scheduler
                        and s.execution_status == _SS.RUNNING
                    ),
                },
            })

    hanging_signals: dict[str, int] = {}
    try:
        from ...core.study.hanging_events import HangingEventsStore
        with HangingEventsStore() as hes:
            hanging_signals = hes.count_since(session_id=session_id, hours=24)
    except Exception:
        # HangingEventsStore not yet wired (C1) -- leave an empty dict so
        # the endpoint contract is stable.
        hanging_signals = {}

    return {
        "status": "ok",
        "session_id": session_id,
        "db_path": str(sched.store.db_path),
        "generated_at": now,
        "watchdog": sched.dump_watchdog(),
        "concurrency": sched.dump_concurrency(),
        "session_queues": sched.dump_session_queues(),
        "studies": studies_payload,
        "hanging_signals_in_window": hanging_signals,
    }


@router.get("/_internal/dump")
async def study_internal_dump(
    session_id: str = Query(...),
    study_id: str | None = None,
    x_admin_token: str | None = Header(None),
) -> dict:
    """Operator dump: all scheduler + DB state for a session.

    Requires ``X-Admin-Token`` (reuse the admin auth helper). Use this
    when triaging a stuck study instead of grepping logs. The intended
    consumers are the ops runbook and the on-call engineer.

    Topics covered:
    - scheduler watchdog (alive / interval / heartbeat threshold)
    - global concurrency (semaphore, queue, active sets)
    - per-session queue depth + consumer health
    - per-study lifecycle (round, heartbeat age, last_error, hanging
      protection flags)
    - last-24h hanging-protection event counts (C1; empty until then)
    """
    from .admin import _verify_admin
    _verify_admin(x_admin_token)
    return await _study_dump(session_id=session_id, study_id=study_id)


# ── request bodies ──────────────────────────────────────────────────


class MetricTargetModel(BaseModel):
    name: str
    op: str = ">="
    value: float


class StudyStartRequest(BaseModel):
    session_id: str
    objective: str
    workspace_path: str
    strategy_name: str
    executor_type: str = "autoresearch"  # "autoresearch" (default, round-based) or "workflow" (DAG)
    metric_targets: Optional[list[MetricTargetModel]] = Field(
        None, max_length=16,
    )
    budget_token: Optional[int] = None
    budget_turn: Optional[int] = None
    budget_time_seconds: Optional[int] = None
    cooldown_base: float = 30.0
    cooldown_jitter: float = 10.0
    min_cooldown: float = 1.0
    max_rounds: Optional[int] = None
    early_stop_patience: int = 3
    behavior: Optional[str] = None
    lazy_detection_interval: int = 10
    keep_recent: int = 10
    monitor_interval_seconds: Optional[int] = None
    guidance_md: Optional[str] = None  # v2: per-task guidance override (§13)
    auto_compose_graph: bool = False  # P6: LLM-generate graph from objective
    selected_agents: Optional[list[str]] = None  # P6: manual override of planner
    graph_override: Optional[dict] = None  # P6: raw graph.json to persist


class DirectiveRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)
    issued_by: Optional[str] = Field(None, max_length=128)


class CancelRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=512)


def _serialize_round(r) -> dict:
    """Shared round wire shape (used by summary + rounds list).

    Single source of truth so both endpoints return identical fields
    (round_num, run_name, metrics, verdict, review, error,
    factor_failures, verdict_reason, created_at).
    """
    return {
        "round_num": r.round_num,
        "run_name": r.run_name,
        "metrics": r.metrics,
        "verdict": r.verdict,
        "review": getattr(r, "review", None),
        "error": getattr(r, "error", None),
        "factor_failures": getattr(r, "factor_failures", None) or [],
        "verdict_reason": getattr(r, "verdict_reason", None),
        "created_at": r.created_at,
    }


# ── POST /study/start ───────────────────────────────────────────────


@router.post("/start", response_model=StudyStartResponse)
async def study_start(req: StudyStartRequest, request: Request):
    """Create a study + its goal ledger row + queue the executor.

    Returns ``{study_id, goal_id, status:"queued"}``.
    """
    # Security: enforce session ownership (IDOR protection).
    from .web_session import _fetch_session_owned, _get_db

    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), req.session_id, user_id)

    print(f"[STUDY:api] POST /study/start session={req.session_id} "
          f"strategy={req.strategy_name} objective={req.objective[:40]}",
          flush=True)

    from ...core.study.bootstrap import create_study_record

    try:
        if req.executor_type == "workflow":
            # E3: split — workflow execution has its own router
            # (``POST /api/goal/workflow/start``, workflow.py). This
            # endpoint is round-based autoresearch only.
            raise HTTPException(
                status_code=400,
                detail=(
                    "executor_type='workflow' is not supported by /study/start; "
                    "use POST /api/goal/workflow/start for DAG workflows"
                ),
            )
        from ...core.study import StudyStatus
        # AEGIS: round-based AutoresearchRunner via the scheduler.
        # Shared orchestration (validation / ledger / autonomous dir)
        # lives in core/study/bootstrap.py.
        study = create_study_record(
            owner_session_id=req.session_id,
            objective=req.objective,
            workspace_path=req.workspace_path,
            strategy_name=req.strategy_name,
            metric_targets=(
                [t.model_dump() for t in req.metric_targets]
                if req.metric_targets else None
            ),
            budget_token=req.budget_token,
            budget_turn=req.budget_turn,
            budget_time_seconds=req.budget_time_seconds,
            cooldown_base=req.cooldown_base,
            cooldown_jitter=req.cooldown_jitter,
            min_cooldown=req.min_cooldown,
            max_rounds=req.max_rounds,
            early_stop_patience=req.early_stop_patience,
            behavior=req.behavior,
            monitor_interval_seconds=req.monitor_interval_seconds,
            guidance_md=req.guidance_md,
            lazy_detection_interval=req.lazy_detection_interval,
            keep_recent=req.keep_recent,
            auto_compose_graph=req.auto_compose_graph,
            selected_agents=req.selected_agents,
        )
        # Queue without blocking the request; uncaught submit errors
        # are logged via the done callback.
        sched = _get_study_scheduler()
        import asyncio
        task = asyncio.create_task(sched.submit(study))
        task.add_done_callback(log_task_exception)
        return {
            "status": "ok",
            "study_id": study.study_id,
            "goal_id": study.goal_id,
            "session_id": study.study_id,
            "execution_status": StudyStatus.QUEUED.value,
            "executor_type": "autoresearch",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("study start failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /study/status, /study/list ───────────────────────────────────


@router.get("/list", response_model=StudyListResponse)
async def study_list(
    request: Request,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    before_created_at: Optional[str] = None,
    include_archived: bool = False,
):
    """List studies, optionally filtered by session/status.

    When session_id is provided, enforces ownership (IDOR).
    ``before_created_at`` enables keyset pagination: pass the
    ``created_at`` of the last row from the previous page.
    ``include_archived=False`` (default) hides soft-deleted studies;
    pass True to surface them in the dedicated UI toggle.
    """
    if session_id:
        # Security: only allow listing studies for sessions the caller
        # owns.
        from .web_session import _fetch_session_owned, _get_db
        user_id = getattr(request.state, "user_id", None) or "anonymous"
        _fetch_session_owned(_get_db(), session_id, user_id)
    from ...core.study import StudyStatus, StudyStore
    try:
        status_enum = StudyStatus(status) if status else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    with StudyStore() as store:
        if session_id:
            rows = store.list_studies(
                session_id=session_id, status=status_enum,
                limit=limit, before_created_at=before_created_at,
                include_archived=include_archived,
            )
        else:
            # No session filter: scope to the caller's own sessions (IDOR).
            import os as _os
            enforce = _os.environ.get("SR_ENFORCE_STUDY_IDOR", "1") != "0"
            if not enforce:
                rows = store.list_studies(
                    session_id=None, status=status_enum,
                    limit=limit, before_created_at=before_created_at,
                    include_archived=include_archived,
                )
            else:
                user_id = getattr(request.state, "user_id", None) or "anonymous"
                owner_sids = _user_session_ids(user_id)
                rows = store.list_studies_for_owner_sessions(
                    owner_sids, status=status_enum,
                    limit=limit, before_created_at=before_created_at,
                    include_archived=include_archived,
                )
    def _shape(r):
        return {
            "study_id": r.study_id, "session_id": r.session_id,
            "goal_id": r.goal_id, "objective": r.objective,
            "strategy_name": r.strategy_name, "workspace_path": r.workspace_path,
            "execution_status": r.execution_status.value,
            "current_round": r.current_round,
            "last_verdict": r.last_verdict,
            "last_metrics": r.last_metrics,
            "last_error": r.last_error,
            "created_at": r.created_at, "updated_at": r.updated_at,
            "completed_at": r.completed_at,
            "archived_at": r.archived_at,
            "archived_by": r.archived_by,
        }
    # Keyset cursor: expose the last row's created_at so the client can
    # fetch older pages (None when the page is exhausted).
    next_cursor = rows[-1].created_at if len(rows) == limit else None
    return {"status": "ok", "studies": [_shape(r) for r in rows],
            "next_cursor": next_cursor}


@router.get("/status", response_model=StudyStatusResponse, response_model_exclude_none=True)
async def study_status(
    request: Request,
    session_id: str = Query(...),
    study_id: Optional[str] = None,
):
    """Return the active study (or one by id) for a session + its goal snapshot.

    Security: enforces session ownership before reading the session's
    studies. When `study_id` is also provided, verifies that the
    study belongs to the queried session (cross-session IDOR block).
    """
    print(f"[STUDY:api] GET /study/status session={session_id} study_id={study_id}",
          flush=True)
    # Security: enforce session ownership.
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), session_id, user_id)
    from ...core.goal import GoalStore
    from ...core.study import StudyStore
    with StudyStore() as store:
        if study_id:
            study = store.get_study(study_id)
            if study is None:
                return {"status": "no_study", "session_id": session_id}
            # Defense in depth: the study must belong to this session
            # (v2 single identity: ownership is the owner_session_id).
            if study.owner_session_id != session_id:
                raise HTTPException(
                    status_code=403, detail="Study does not belong to this session",
                )
        else:
            study = store.get_active_study(session_id)
        if study is None:
            return {"status": "no_study", "session_id": session_id}
        # Goal snapshot for criteria/progress/evidence_counts
        goal_snapshot = None
        if study.goal_id:
            with GoalStore() as gs:
                goal_snapshot = gs.get_goal_snapshot(study.goal_id)
    return {
        "status": "ok",
        "study_id": study.study_id,
        "goal_id": study.goal_id,
        "execution_status": study.execution_status.value,
        "current_round": study.current_round,
        "objective": study.objective,
        "workspace_path": study.workspace_path,
        "strategy_name": study.strategy_name,
        "metric_targets": study.metric_targets,
        "last_metrics": study.last_metrics,
        "last_verdict": study.last_verdict,
        "last_error": study.last_error,
        "heartbeat": study.heartbeat,
        "created_at": study.created_at,
        "updated_at": study.updated_at,
        "completed_at": study.completed_at,
        "goal_snapshot": _snapshot(goal_snapshot),
    }


def _snapshot(s: dict | None) -> dict | None:
    if not s:
        return None
    g = s.get("goal", {}) or {}
    return {
        "goal_id": g.get("goal_id"),
        "goal_status": g.get("status"),
        "objective": g.get("objective"),
        "progress_percent": g.get("progress_percent", 0),
        "evidence_count": s.get("evidence_count", 0),
        "criteria": [
            {"criterion_id": c.get("criterion_id"), "text": c.get("text"),
             "status": c.get("status"), "required": c.get("required", True)}
            for c in s.get("criteria", []) or []
        ],
    }


# ── GET /study/{study_id}/summary ──────────────────────────────────


@router.get("/{study_id}/summary", response_model=StudySummaryResponse)
async def study_summary(request: Request, study_id: str):
    """Return study summary with recent rounds, scoreboard, and goal snapshot."""
    # E1: study-id-derived endpoints enforce ownership when
    # SR_ENFORCE_STUDY_IDOR=1 (multi-tenant). Single-user workspaces
    # keep working without per-study session rows.
    study = _owned_study(request, study_id)
    from ...core.goal import GoalStore
    from ...core.study import StudyStore
    with StudyStore() as store:
        # Recent rounds (last 5)
        recent_rounds = store.list_rounds(study_id, limit=5)

        # Goal snapshot
        goal_snapshot = None
        if study.goal_id:
            with GoalStore() as gs:
                goal_snapshot = gs.get_goal_snapshot(study.goal_id)

                # Journal entries (last 10)
                journal_entries = gs.list_journal_entries(study.goal_id, limit=10)

                # Scoreboard from journal
                scoreboard = _build_scoreboard(journal_entries)
        else:
            journal_entries = []
            scoreboard = []

    # Load budget data from state.json
    budget_data = None
    try:
        from ...core.study.state_store import load as load_state
        state = load_state(Path(study.workspace_path), study.study_id)
        budget_data = {
            "budget_used_turns": state.budget_used_turns,
            "budget_used_time_s": state.budget_used_time_s,
            "budget_turn": study.budget_turn,
            "budget_time_seconds": study.budget_time_seconds,
        }
    except Exception:
        pass

    return {
        "status": "ok",
        "study_id": study.study_id,
        "execution_status": study.execution_status.value,
        "current_round": study.current_round,
        "max_rounds": study.max_rounds,
        "objective": study.objective,
        "strategy_name": study.strategy_name,
        "workspace_path": study.workspace_path,
        "metric_targets": study.metric_targets,
        "created_at": study.created_at,
        "updated_at": study.updated_at,
        "completed_at": study.completed_at,
        "last_metrics": study.last_metrics,
        "last_verdict": study.last_verdict,
        "last_error": study.last_error,
        "last_traceback": study.last_traceback,
        "archived_at": study.archived_at,
        "archived_by": study.archived_by,
        "recent_rounds": [_serialize_round(r) for r in recent_rounds],
        "scoreboard": scoreboard,
        "goal_snapshot": _snapshot(goal_snapshot),
        "budget": budget_data,
        "monitor_state": (
            {
                "drift_count": study.monitor_drift_count,
                "last_check_at": study.last_monitor_check_at,
                "interval_seconds": study.monitor_interval_seconds,
            }
            if study.monitor_interval_seconds is not None else None
        ),
    }


def _build_scoreboard(journal_entries: list) -> list[dict]:
    """Build lever scoreboard from journal entries."""
    from collections import defaultdict
    lever_stats: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "accepted": 0, "reverted": 0})

    for entry in journal_entries:
        # Handle both JournalEntry objects and dicts
        levers = getattr(entry, "levers", None) or (entry.get("levers", []) if isinstance(entry, dict) else [])
        outcome = getattr(entry, "gating_outcome", "") or (entry.get("gating_outcome", "") if isinstance(entry, dict) else "")
        for lever in levers:
            lever_stats[lever]["attempts"] += 1
            if outcome == "accepted":
                lever_stats[lever]["accepted"] += 1
            elif outcome == "reverted":
                lever_stats[lever]["reverted"] += 1

    scoreboard = []
    for lever, stats in sorted(lever_stats.items(), key=lambda x: -x[1]["attempts"]):
        attempts = stats["attempts"]
        accepted = stats["accepted"]
        precision = accepted / attempts if attempts > 0 else 0.0
        scoreboard.append({
            "lever": lever,
            "precision_mean": round(precision, 2),
            "attempts": attempts,
            "accepted": accepted,
            "reverted": stats["reverted"],
        })
    return scoreboard


# ── POST /study/{study_id}/pause|resume|cancel ──────────────────────


def _study_session_id(study_id: str) -> str | None:
    """Look up a study's OWNER session (creator chat session) for IDOR.

    v2 single identity: the study's ``session_id`` column equals the
    study_id (no sessions row exists for it) — ownership is verified via
    ``owner_session_id`` (the creator's chat session, which has a row).
    """
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
    return study.owner_session_id if study else None


@router.post("/{study_id}/pause", response_model=StudyActionResponse)
async def study_pause(request: Request, study_id: str):
    _owned_study(request, study_id)  # IDOR: caller must own the study
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not active")
    from ...core.study import StudyStatus, StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
    if study is not None and study.execution_status not in (
        StudyStatus.RUNNING, StudyStatus.MONITORING,
    ):
        # Not in a pausable state (e.g. INTERRUPTED, QUEUED, terminal):
        # 409 Conflict — the UI must not pretend the pause took effect.
        raise HTTPException(
            status_code=409,
            detail=f"study not pausable in state {study.execution_status.value}",
        )
    sched = _get_study_scheduler()
    if not sched.pause(study_id):
        raise HTTPException(status_code=409, detail="study not pausable")
    return {"status": "ok", "study_id": study_id, "action": "paused"}


@router.post("/{study_id}/resume", response_model=StudyActionResponse)
async def study_resume(request: Request, study_id: str):
    """Resume a paused or interrupted study."""
    _owned_study(request, study_id)  # IDOR: caller must own the study
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not found")

    sched = _get_study_scheduler()

    # Check current status to decide resume path
    from ...core.study import StudyStatus, StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="study not found")

    if study.execution_status == StudyStatus.INTERRUPTED:
        # Re-submit to scheduler
        if not await sched.resume_interrupted(study_id):
            raise HTTPException(status_code=400, detail="failed to resume interrupted study")
        return {"status": "ok", "study_id": study_id, "action": "resumed_from_interrupted"}

    # PAUSED: unpause existing runner
    if not sched.resume(study_id):
        raise HTTPException(status_code=404, detail="study not found or not paused")
    return {"status": "ok", "study_id": study_id, "action": "resumed"}


@router.post("/{study_id}/cancel", response_model=StudyActionResponse)
async def study_cancel(request: Request, study_id: str, req: CancelRequest | None = None):
    _owned_study(request, study_id)  # IDOR: caller must own the study
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not active")
    sched = _get_study_scheduler()
    reason = req.reason if req else None
    if not sched.cancel(study_id, reason=reason):
        raise HTTPException(status_code=404, detail="study not active")
    return {"status": "ok", "study_id": study_id, "action": "cancelled"}


# ── POST /study/{study_id}/directive (Phase 2: mid-exec interaction) ──


@router.post("/{study_id}/directive", response_model=StudyDirectiveCreatedResponse)
async def study_directive(request: Request, study_id: str, req: DirectiveRequest):
    """Inject a user-issued directive into the study's next round.

    Persists to ``study_directives``; the executor's per-round loop
    consumes it and emits ``study_directives_consumed`` once the
    researcher agent has seen it.
    """
    _owned_study(request, study_id)  # IDOR: caller must own the study
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not found")

    from ...core.study import StudyStore
    try:
        with StudyStore() as store:
            directive = store.add_directive(
                study_id=study_id, content=req.content,
                issued_by=req.issued_by,
            )
    except ValueError as exc:
        detail = str(exc)
        code = 400 if "content" in detail else 404
        raise HTTPException(status_code=code, detail=detail)
    # Best-effort emit so the chat panel reflects the directive.
    sched = _get_study_scheduler()
    if sched.session_service is not None and sched.session_service.event_bus is not None:
        try:
            sched.session_service.event_bus.emit(
                "", "study_directive_added", {
                    "study_id": study_id,
                    "directive_id": directive.directive_id,
                    "content": directive.content,
                    "issued_by": directive.issued_by,
                    "created_at": directive.created_at,
                },
            )
        except Exception:
            pass
    return {
        "status": "ok",
        "study_id": study_id,
        "directive_id": directive.directive_id,
        "created_at": directive.created_at,
    }


@router.get("/{study_id}/directives", response_model=StudyDirectivesResponse)
async def study_directives_list(request: Request, study_id: str):
    """List pending + consumed directives for a study (audit trail)."""
    _owned_study(request, study_id)  # IDOR: caller must own the study
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")
        rows = store.list_directives(study_id, limit=50)
    return {
        "status": "ok",
        "study_id": study_id,
        "directives": [
            {
                "directive_id": r.directive_id,
                "content": r.content,
                "issued_by": r.issued_by,
                "created_at": r.created_at,
                "consumed_at": r.consumed_at,
            }
            for r in rows
        ],
    }

# ── v2 artifacts endpoints (design §17) ────────────────────────────────


def _user_session_ids(user_id: str) -> list[str]:
    """Return the session ids owned by ``user_id`` (for study scoping)."""
    from .web_session import _get_db
    conn = _get_db()
    rows = conn.execute(
        "SELECT id FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchall()
    return [r["id"] for r in rows]


def _owned_study(request: Request, study_id: str):
    """Fetch a study with IDOR enforcement (owner-session based).

    The study's ``owner_session_id`` must belong to the authenticated user
    (a session row owned by ``request.state.user_id``); otherwise 403.
    Enforcement is on by default (``SR_ENFORCE_STUDY_IDOR`` defaults to
    ``"1"``); set ``SR_ENFORCE_STUDY_IDOR=0`` to disable for single-user
    workspaces.
    """
    import os as _os

    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")

    enforce = _os.environ.get("SR_ENFORCE_STUDY_IDOR", "1") != "0"
    if enforce:
        user_id = getattr(request.state, "user_id", None) or "anonymous"
        owner_sid = study.owner_session_id
        if owner_sid:
            from .web_session import _fetch_session_owned, _get_db
            try:
                _fetch_session_owned(_get_db(), owner_sid, user_id)
            except HTTPException as exc:
                if exc.status_code == 403:
                    raise HTTPException(
                        status_code=403, detail="Study does not belong to this user",
                    )
                raise
        elif user_id != "anonymous":
            raise HTTPException(
                status_code=403, detail="Study does not belong to this user",
            )
    return study


@router.get("/{study_id}/rounds", response_model=StudyRoundsResponse)
async def study_rounds_list(
    request: Request, study_id: str,
    offset: int = 0, limit: int = 20,
):
    """v2: paginated round history (study_rounds table)."""
    from ...core.study import StudyStore
    _owned_study(request, study_id)
    with StudyStore() as store:
        rows = store.list_rounds(study_id, limit=offset + limit)
        total = store.count_rounds(study_id)
    page = rows[offset:offset + limit]
    return {
        "status": "ok",
        "study_id": study_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rounds": [_serialize_round(r) for r in page],
    }


@router.get("/{study_id}/journal", response_model=StudyJournalResponse)
async def study_journal(request: Request, study_id: str):
    """v2: journal.md content (append-only archive, single source)."""
    from ...core.study.round_manifest import journal_path
    study = _owned_study(request, study_id)
    p = journal_path(Path(study.workspace_path), study_id)
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"status": "ok", "study_id": study_id, "journal": content}


@router.get("/{study_id}/todos", response_model=StudyTodosResponse)
async def study_todos(request: Request, study_id: str):
    """v2: todos.md content (task tracking for the study)."""
    study = _owned_study(request, study_id)
    p = Path(study.workspace_path) / "study" / study_id / "todos.md"
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"status": "ok", "study_id": study_id, "todos": content}


@router.get("/{study_id}/knowledge", response_model=StudyKnowledgeResponse)
async def study_knowledge(request: Request, study_id: str):
    """v2: knowledge.md content (accumulated research knowledge)."""
    study = _owned_study(request, study_id)
    p = Path(study.workspace_path) / "study" / study_id / "knowledge.md"
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"status": "ok", "study_id": study_id, "knowledge": content}


@router.get("/{study_id}/guidance", response_model=StudyGuidanceResponse)
async def study_guidance(request: Request, study_id: str):
    """v2: guidance.md content + parsed gates (design §13.4)."""
    from ...core.study import guidance as gd
    study = _owned_study(request, study_id)
    g = gd.load_guidance(Path(study.workspace_path), study_id)
    if g.source is None:
        raise HTTPException(status_code=404, detail="guidance not found")
    return {
        "status": "ok",
        "study_id": study_id,
        "source": str(g.source),
        "task_scope": g.task_scope,
        "gates": [gate.to_dict() for gate in g.gates],
        "body": g.body,
        "text": g.source.read_text(encoding="utf-8"),
    }


@router.get("/{study_id}/rounds/{round_num}/summary_md", response_model=StudyRoundSummaryMdResponse)
async def study_round_summary_md(request: Request, study_id: str, round_num: int):
    """v2: single-round summary.md content."""
    from ...core.study import round_manifest as rm
    study = _owned_study(request, study_id)
    p = rm.summary_path(Path(study.workspace_path), study_id, round_num)
    if not p.exists():
        raise HTTPException(status_code=404, detail="round summary not found")
    return {
        "status": "ok",
        "study_id": study_id,
        "round": round_num,
        "summary_md": p.read_text(encoding="utf-8"),
    }


# ── Phase 3: round detail / artifacts / diff / adopt ────────────────


def _round_run_dirs(workspace_path: Path, study_id: str, round_num: int) -> list[Path]:
    """All ``run_*`` directories inside a round dir (study layout)."""
    from ...core.study import round_manifest as rm
    rd = rm.round_dir(workspace_path, study_id, round_num)
    if not rd.exists():
        return []
    return [d for d in rd.iterdir() if d.is_dir() and d.name.startswith("run_")]


@router.get("/{study_id}/rounds/{round_num}/artifacts", response_model=StudyRoundArtifactsResponse)
async def study_round_artifacts(request: Request, study_id: str, round_num: int):
    """List files produced by a round (round dir + run dirs), newest first.

    Files: manifest.json / summary.md at round root, plus everything
    under the run_* directories (strategy.py, config.yaml, agents/*,
    backtest logs...).
    """
    study = _owned_study(request, study_id)
    from ...core.study import round_manifest as rm
    rd = rm.round_dir(Path(study.workspace_path), study_id, round_num)
    if not rd.exists():
        raise HTTPException(status_code=404, detail="round dir not found")
    artifacts: list[dict] = []
    for p in sorted(rd.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True):
        if p.is_file():
            st = p.stat()
            artifacts.append({
                "path": str(p.relative_to(rd)),
                "size": st.st_size,
                "mtime": str(st.st_mtime),
            })
    return {
        "status": "ok",
        "study_id": study_id,
        "round": round_num,
        "round_dir": str(rd.relative_to(Path(study.workspace_path))),
        "artifacts": artifacts,
    }


@router.get("/{study_id}/rounds/{round_num}/manifest", response_model=StudyRoundManifestResponse)
async def study_round_manifest(request: Request, study_id: str, round_num: int):
    """Round manifest.json content (hypothesis / changes / metrics / next)."""
    study = _owned_study(request, study_id)
    from ...core.study import round_manifest as rm
    m = rm.load_manifest(Path(study.workspace_path), study_id, round_num)
    if m is None:
        raise HTTPException(status_code=404, detail="round manifest not found")
    return {"status": "ok", "study_id": study_id, "round": round_num, "manifest": m}


@router.get(
    "/{study_id}/rounds/{round_num}/agent_outputs",
    response_model=StudyRoundAgentOutputsResponse,
)
async def study_round_agent_outputs(
    request: Request, study_id: str, round_num: int,
):
    """Agent chat outputs for a round.

    Reads ``{rounds_dir}/round_{N}/run_{latest}/agents/*.json`` and
    returns a dict keyed by agent name. Each value contains the full
    agent JSON (output, input, duration_ms, etc.).
    """
    study = _owned_study(request, study_id)
    from pathlib import Path
    ws = Path(study.workspace_path).resolve()
    rounds_dir = ws / "study" / study_id / "rounds"
    round_dir = rounds_dir / f"round_{round_num:04d}"
    if not round_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"round {round_num} not found")

    # Find the latest run directory (run_NNNN)
    run_dirs = sorted(
        [d for d in round_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda d: d.name,
    )
    if not run_dirs:
        raise HTTPException(status_code=404, detail=f"no runs found for round {round_num}")

    agents_dir = run_dirs[-1] / "agents"
    agent_outputs: dict = {}
    if agents_dir.is_dir():
        import json
        for agent_file in sorted(agents_dir.glob("*.json")):
            agent_name = agent_file.stem
            try:
                data = json.loads(agent_file.read_text(encoding="utf-8"))
                agent_outputs[agent_name] = data
            except (json.JSONDecodeError, OSError):
                pass

    return {
        "status": "ok",
        "study_id": study_id,
        "round": round_num,
        "agent_outputs": agent_outputs,
    }


@router.get("/{study_id}/rounds/{round_num}/diff", response_model=StudyRoundDiffResponse)
async def study_round_diff(
    request: Request, study_id: str, round_num: int,
    against: int = Query(0, ge=0),
):
    """Unified diff of the adopted strategy.py between two rounds.

    ``against=0`` diffs against the baseline strategy (ws/strategies/
    <name>/baseline/strategy.py). Otherwise diffs round ``against``'s
    adopted run strategy vs round ``round_num``'s.
    """
    study = _owned_study(request, study_id)
    import difflib
    ws = Path(study.workspace_path)

    def _strategy_of(rn: int) -> Path | None:
        if rn == 0:
            base = ws / "strategies" / study.strategy_name / "baseline" / "strategy.py"
            return base if base.exists() else None
        runs = _round_run_dirs(ws, study_id, rn)
        if not runs:
            return None
        # adopted strategy is in the round's (single) run dir
        return runs[0] / "strategy.py"

    pa = _strategy_of(against)
    pb = _strategy_of(round_num)
    if pa is None or pb is None:
        raise HTTPException(status_code=404, detail="strategy.py not found for diff")
    la = pa.read_text(encoding="utf-8").splitlines()
    lb = pb.read_text(encoding="utf-8").splitlines()
    lines = []
    adds = dels = ctx = 0
    for line in difflib.unified_diff(la, lb, fromfile=f"round_{against}", tofile=f"round_{round_num}", lineterm=""):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            lines.append({"line": line[1:], "kind": "add"})
            adds += 1
        elif line.startswith("-"):
            lines.append({"line": line[1:], "kind": "del"})
            dels += 1
        else:
            lines.append({"line": line, "kind": "context"})
            ctx += 1
    return {
        "status": "ok",
        "study_id": study_id,
        "round_a": against,
        "round_b": round_num,
        "diff": lines,
        "stats": {"adds": adds, "dels": dels, "context": ctx},
    }


@router.post("/{study_id}/rounds/{round_num}/adopt", response_model=StudyAdoptResponse)
async def study_round_adopt(request: Request, study_id: str, round_num: int):
    """Adopt a round's strategy.py as the new round-start baseline.

    NON-DESTRUCTIVE: copies the round's adopted run strategy.py into the
    study root's baseline dir (``ws/study/{id}/baseline/strategy.py``),
    overwriting the study's own baseline — the strategies/<name>/baseline
    (shared across studies) is left untouched. Safe to call while the
    study is paused/interrupted.
    """
    study = _owned_study(request, study_id)
    ws = Path(study.workspace_path)
    runs = _round_run_dirs(ws, study_id, round_num)
    if not runs:
        raise HTTPException(status_code=404, detail="round run dir not found")
    src = runs[0] / "strategy.py"
    if not src.exists():
        raise HTTPException(status_code=404, detail="round strategy.py not found")
    study_baseline = ws / "study" / study_id / "baseline"
    study_baseline.mkdir(parents=True, exist_ok=True)
    study_baseline.joinpath("strategy.py").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8",
    )
    return {
        "status": "ok",
        "study_id": study_id,
        "round": round_num,
        "adopted_run_dir": str(runs[0].relative_to(ws)),
        "note": "copied to study baseline; next resume will start from this round's strategy",
    }


# ── Phase 4: per-study hanging events ───────────────────────────────


@router.get("/{study_id}/hanging_events", response_model=StudyHangingEventsResponse)
async def study_hanging_events(
    request: Request, study_id: str,
    hours: float = Query(24, ge=1, le=24 * 30),
    limit: int = Query(20, ge=1, le=100),
):
    """Recent watchdog / stall / breaker events for one study.

    The UI shows a badge count + recent list so operators can tell at a
    glance whether a study has been killed by the watchdog vs. completed
    normally.
    """
    _owned_study(request, study_id)
    from ...core.study import hanging_events as he
    with he.HangingEventsStore() as store:
        by_type = store.count_since(study_id=study_id, hours=hours)
        recent = store.list_recent(study_id=study_id, hours=hours, limit=limit)
    return {
        "status": "ok",
        "study_id": study_id,
        "window_hours": hours,
        "by_type": by_type,
        "recent": recent,
    }


# ── Phase 5: action matrix (state-machine v2) ───────────────────────


# Human labels + destructive flags for the action matrix.
_ACTION_META: dict[str, dict] = {
    "pause": {"label": "暂停", "destructive": False},
    "resume": {"label": "恢复", "destructive": False},
    "resume_interrupted": {"label": "恢复（重新排队）", "destructive": False},
    "cancel": {"label": "中止", "destructive": True},
    "redo": {"label": "重跑本轮", "destructive": True},
    "archive": {"label": "归档", "destructive": True},
    "unarchive": {"label": "取消归档", "destructive": False},
    "replace_objective": {"label": "修改目标", "destructive": False},
    "retry": {"label": "重试", "destructive": False},
}


@router.get("/{study_id}/available_actions", response_model=StudyAvailableActionsResponse)
async def study_available_actions(request: Request, study_id: str):
    """Actions the current status permits (drives the UI's buttons)."""
    from ...core.study import StudyStore
    from ...core.study.models import allowed_actions
    _owned_study(request, study_id)
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")
    status = study.execution_status.value
    actions = [
        StudyActionItem(name=a.value, **_ACTION_META[a.value])
        for a in allowed_actions(study.execution_status)
        if a.value in _ACTION_META
    ]
    return {
        "status": "ok",
        "study_id": study_id,
        "execution_status": status,
        "actions": actions,
    }


@router.post("/{study_id}/actions/{action_name}", response_model=StudyActionResponse)
async def study_dispatch_action(
    request: Request, study_id: str, action_name: str,
    body: StudyActionRequest | None = None,
):
    """Unified action entrypoint: pause / resume / resume_interrupted /
    cancel / redo (see ``GET /available_actions`` for what's allowed now).

    Returns 409 when the action is not allowed in the current status —
    the UI must render buttons only from ``available_actions``.
    """
    from ...core.study import StudyStore
    _owned_study(request, study_id)
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")

    from ...core.study.models import StudyAction, allowed_actions
    try:
        act = StudyAction(action_name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown action: {action_name}")
    if act not in allowed_actions(study.execution_status):
        raise HTTPException(
            status_code=409,
            detail=f"action '{action_name}' not allowed in state {study.execution_status.value}",
        )

    sched = _get_study_scheduler()
    reason = body.reason if body else None

    if act == StudyAction.PAUSE:
        if not sched.pause(study_id):
            raise HTTPException(status_code=409, detail="study not pausable")
        return {"status": "ok", "study_id": study_id, "action": "paused"}
    if act == StudyAction.RESUME:
        if not sched.resume(study_id):
            raise HTTPException(status_code=409, detail="study not resumable")
        return {"status": "ok", "study_id": study_id, "action": "resumed"}
    if act == StudyAction.RESUME_INTERRUPTED:
        if not await sched.resume_interrupted(study_id):
            raise HTTPException(status_code=409, detail="study not resumable")
        return {"status": "ok", "study_id": study_id, "action": "resumed_from_interrupted"}
    if act == StudyAction.CANCEL:
        if not sched.cancel(study_id, reason=reason):
            raise HTTPException(status_code=409, detail="study not cancellable")
        return {"status": "ok", "study_id": study_id, "action": "cancelled"}
    if act == StudyAction.ARCHIVE:
        archived_by = body.archived_by if body else None
        if not sched.archive(study_id, archived_by=archived_by, reason=reason):
            raise HTTPException(status_code=409, detail="study not archivable (already archived?)")
        return {"status": "ok", "study_id": study_id, "action": "archived"}
    if act == StudyAction.UNARCHIVE:
        if not sched.unarchive(study_id):
            raise HTTPException(status_code=409, detail="study not archived")
        return {"status": "ok", "study_id": study_id, "action": "unarchived"}
    if act == StudyAction.REDO:
        round_num = body.round_num if body and body.round_num else study.current_round
        if study.execution_status == StudyStatus.RUNNING:
            raise HTTPException(status_code=409, detail="study is running; pause or cancel first")
        ok = await sched.redo(study_id, round_num, workspace_path=study.workspace_path)
        if not ok:
            raise HTTPException(status_code=409, detail="redo failed")
        return {"status": "ok", "study_id": study_id, "action": f"redo_round_{round_num}"}
    if act == StudyAction.REPLACE_OBJECTIVE:
        new_obj = body.new_objective if body else None
        expected_gid = body.expected_goal_id if body else None
        if not new_obj or not expected_gid:
            raise HTTPException(
                status_code=400,
                detail="replace_objective requires 'new_objective' and 'expected_goal_id'",
            )
        try:
            res = sched.replace_objective(
                study_id,
                new_objective=new_obj,
                expected_goal_id=expected_gid,
                replaced_by=(body.archived_by if body else None) or "user",
                reason=reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if res is None:
            raise HTTPException(status_code=404, detail="study not found")
        return {
            "status": "ok",
            "study_id": study_id,
            "action": f"replaced_objective_history_{res['history_id']}",
        }
    if act == StudyAction.RETRY:
        from_round = body.from_round if body else None
        mode = body.mode if body else "append"
        if not sched.retry(study_id, from_round=from_round, mode=mode):
            raise HTTPException(
                status_code=409,
                detail="study not retryable (not in a retryable terminal state?)",
            )
        return {
            "status": "ok",
            "study_id": study_id,
            "action": "retry_queued",
        }
    raise HTTPException(status_code=404, detail=f"action '{action_name}' not implemented")


@router.get("/{study_id}/objective_history",
            response_model=StudyObjectiveHistoryResponse)
async def study_objective_history(request: Request, study_id: str):
    """Audit trail of objective replacements (newest first)."""
    from ...core.study import StudyStore
    _owned_study(request, study_id)
    with StudyStore() as store:
        if store.get_study(study_id) is None:
            raise HTTPException(status_code=404, detail="study not found")
        entries = store.list_objective_history(study_id)
    return {
        "status": "ok",
        "study_id": study_id,
        "history": [
            {
                "id": e.id,
                "study_id": e.study_id,
                "session_id": e.session_id,
                "objective": e.objective,
                "replaced_by": e.replaced_by,
                "expected_goal_id": e.expected_goal_id,
                "reason": e.reason,
                "applied_at": e.applied_at,
                "applied_round": e.applied_round,
            }
            for e in entries
        ],
    }


@router.get("/{study_id}/graph", response_model=StudyGraphResponse)
async def study_graph(request: Request, study_id: str):
    """Return the study's execution graph (nodes + edges).

    Reads ``{ws}/study/{id}/graph.json``. Falls back to the standard
    8-node template when missing (legacy studies; see migration
    script) — the response sets ``persisted=False`` in that case.
    """
    from pathlib import Path as _Path
    from ...core.study.graph import StudyGraph
    from ...core.study.graph_templates import DEFAULT_STANDARD_GRAPH
    study = _owned_study(request, study_id)
    ws = _Path(study.workspace_path)
    persisted = True
    graph = StudyGraph.load(ws, study_id)
    if graph is None:
        graph = DEFAULT_STANDARD_GRAPH
        persisted = False
    return {
        "status": "ok",
        "study_id": study_id,
        "graph": {
            "nodes": [n.to_dict() for n in graph.nodes],
            "edges": [e.to_dict() for e in graph.edges],
        },
        "persisted": persisted,
    }


@router.put("/{study_id}/graph", response_model=StudyGraphResponse)
async def study_update_graph(
    request: Request, study_id: str, body: StudyGraphBody,
):
    """Update the study's execution graph.

    Only editable when the study is ``paused`` or ``interrupted`` —
    mutating a running study would orphan its in-flight agent state.
    """
    from ...core.study.graph import StudyGraph
    from ...core.study.models import StudyStatus
    from ...core.study import StudyStore
    study = _owned_study(request, study_id)
    if study.execution_status not in (StudyStatus.PAUSED, StudyStatus.INTERRUPTED):
        raise HTTPException(
            status_code=409,
            detail=(
                f"graph only editable in paused/interrupted state, "
                f"current={study.execution_status.value}"
            ),
        )
    from ...core.study.graph import GraphNode, GraphEdge
    nodes = tuple(
        GraphNode(id=n.id, type=n.type, label=n.label,
                  config=dict(n.config), enabled=n.enabled)
        for n in body.nodes
    )
    edges = tuple(
        GraphEdge(source=e.source, target=e.target, condition=e.condition)
        for e in body.edges
    )
    graph = StudyGraph(nodes=nodes, edges=edges)
    errors = graph.validate()
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    from pathlib import Path as _Path
    graph.save(_Path(study.workspace_path), study_id)
    return {
        "status": "ok",
        "study_id": study_id,
        "graph": {
            "nodes": [n.to_dict() for n in graph.nodes],
            "edges": [e.to_dict() for e in graph.edges],
        },
        "persisted": True,
    }


class AgentApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|reject)$")


class AgentApprovalResponse(BaseModel):
    status: str
    study_id: str
    decision: str
    forwarded: bool


@router.post(
    "/{study_id}/agents/approve",
    response_model=AgentApprovalResponse,
)
async def study_approve_agent_loop(
    request: Request, study_id: str, req: AgentApprovalRequest,
):
    """Resolve a pending agent-loop approval gate.

    The frontend POSTs here after seeing the
    ``agent_approval_requested`` event on the SSE stream. The scheduler
    forwards the decision to the active ``AgentLoop`` which unblocks
    ``_check_no_progress``.

    If no runner is currently waiting on approval (e.g. it has already
    timed out), the call returns ``forwarded: false`` instead of erroring.
    """
    _owned_study(request, study_id)
    sched = _get_study_scheduler()
    forwarded = sched.approve_agent_loop(study_id, req.decision)
    return {
        "status": "ok",
        "study_id": study_id,
        "decision": req.decision,
        "forwarded": forwarded,
    }


@router.post("/{study_id}/rounds/{round_num}/redo", response_model=StudyActionResponse)
async def study_round_redo(
    request: Request, study_id: str, round_num: int,
):
    """Redo round ``round_num``: discard its artifacts + state, re-queue
    the study to start again from round ``round_num - 1``.

    Destructive (removes the round's run dir + DB row); the study must
    not be currently running.
    """
    from ...core.study import StudyStatus, StudyStore
    _owned_study(request, study_id)
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")
        if study.execution_status == StudyStatus.RUNNING:
            raise HTTPException(status_code=409, detail="study is running; pause or cancel first")
    sched = _get_study_scheduler()
    ok = await sched.redo(
        study_id, round_num, workspace_path=study.workspace_path,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="redo failed")
    return {
        "status": "ok", "study_id": study_id,
        "action": f"redo_round_{round_num}",
    }


# ── Agent catalog + DAG planning (P6 unified engine) ─────────────────


@router.get("/agents")
async def study_agents():
    """Return the unified agent catalog available for study DAGs."""
    from ...core.agent.plugin import AgentPlugin
    from ...core.agent.registry import get_default_registry

    registry = get_default_registry()
    return {
        "agents": [p.to_dict() for p in registry.list_plugins()],
        "required": sorted(
            p.id for p in registry.list_plugins() if not p.optional
        ),
    }


@router.post("/plan-dag")
async def study_plan_dag(body: dict):
    """LLM-driven study DAG generation.

    Body::

        {
          "objective": "研究 A 股动量因子",
          "max_agents": 12,
          "force_agents": ["researcher"],
          "exclude_agents": []
        }
    """
    from ...core.study.dag_planner import (
        DAGPlanner,
        PlannerConstraints,
    )

    objective = str(body.get("objective", "")).strip()
    if not objective:
        raise HTTPException(status_code=422, detail="objective is required")
    constraints = PlannerConstraints(
        max_agents=int(body.get("max_agents") or 12),
        exclude_agents=list(body.get("exclude_agents") or []),
        force_agents=list(body.get("force_agents") or []),
    )
    planner = DAGPlanner()
    plan = planner.plan(objective, constraints)
    return {
        "status": "ok",
        "selected_agents": plan.selected_agents,
        "reasoning": plan.reasoning,
        "graph": plan.config.to_study_graph().to_dict(),
        "dag_config": plan.config.to_dict(),
    }


@router.get("/presets")
async def study_presets():
    """List YAML preset names available as planner few-shot candidates."""
    from pathlib import Path

    d = Path(__file__).parent.parent.parent / "core" / "swarm" / "presets"
    if not d.is_dir():
        return {"presets": []}
    presets = sorted(p.stem.replace("goal_", "") for p in d.glob("goal_*.yaml"))
    return {"presets": presets}
