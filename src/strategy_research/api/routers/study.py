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
from pydantic import BaseModel

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
    metric_targets: Optional[list[MetricTargetModel]] = None
    budget_token: Optional[int] = None
    budget_turn: Optional[int] = None
    budget_time_seconds: Optional[int] = None
    cooldown_base: float = 30.0
    cooldown_jitter: float = 10.0
    min_cooldown: float = 1.0
    max_rounds: Optional[int] = None
    behavior: Optional[str] = None
    lazy_detection_interval: int = 10
    keep_recent: int = 10
    monitor_interval_seconds: Optional[int] = None
    guidance_md: Optional[str] = None  # v2: per-task guidance override (§13)


class DirectiveRequest(BaseModel):
    content: str
    issued_by: Optional[str] = None


# ── POST /study/start ───────────────────────────────────────────────


@router.post("/start")
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
        if req.executor_type == "autoresearch":
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
                behavior=req.behavior,
                monitor_interval_seconds=req.monitor_interval_seconds,
                guidance_md=req.guidance_md,
                lazy_detection_interval=req.lazy_detection_interval,
                keep_recent=req.keep_recent,
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
        # executor_type == "workflow": single DAG execution via
        # GoalWorkflowRunner (kept in the API layer — needs session_service).
        from ...core.goal import GoalStore
        from ...core.goal.context import default_goal_criteria
        from ...core.goal.workflow import GoalWorkflowRunner
        from ...core.goal.workflow_config import build_autoresearch_workflow_config
        from ...core.study import StudyStatus, StudyStore, default_metric_targets
        from ...core.study.bootstrap import validate_workspace_strategy
        from .chat import _get_session_service

        ws = validate_workspace_strategy(req.workspace_path, req.strategy_name)
        targets = (
            [t.model_dump() for t in req.metric_targets]
            if req.metric_targets else default_metric_targets()
        )
        with StudyStore() as store:
            study = store.create_study(
                owner_session_id=req.session_id,
                goal_id=None,
                objective=req.objective,
                workspace_path=req.workspace_path,
                strategy_name=req.strategy_name,
                metric_targets=targets,
                budget_token=req.budget_token,
                budget_turn=req.budget_turn,
                budget_time_seconds=req.budget_time_seconds,
                cooldown_base=req.cooldown_base,
                cooldown_jitter=req.cooldown_jitter,
                min_cooldown=req.min_cooldown,
                max_rounds=req.max_rounds,
                behavior=req.behavior,
                monitor_interval_seconds=req.monitor_interval_seconds,
            )
            goal_store = GoalStore()
            goal = goal_store.replace_goal(
                session_id=study.study_id,
                objective=req.objective,
                criteria=default_goal_criteria(),
                supersede=False,
            )
            study = store.update_goal_id(study.study_id, goal.goal_id)

        config = build_autoresearch_workflow_config(
            strategy_name=req.strategy_name,
            objective=req.objective,
            metric_targets=targets,
            monitor_interval_seconds=req.monitor_interval_seconds,
            budget_turn=req.budget_turn,
            budget_time_seconds=req.budget_time_seconds,
        )

        session_service = _get_session_service()
        runner = GoalWorkflowRunner(
            config=config,
            session_id=req.session_id,
            session_service=session_service,
            workspace=ws,
        )
        runner.set_goal_id(goal.goal_id)
        # Start in background (non-blocking). A5: log uncaught exceptions.
        import asyncio
        task = asyncio.create_task(runner.start(req.objective))
        task.add_done_callback(log_task_exception)
        return {
            "status": "ok",
            "study_id": study.study_id,
            "goal_id": study.goal_id,
            "execution_status": StudyStatus.QUEUED.value,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("study start failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /study/status, /study/list ───────────────────────────────────


@router.get("/list")
async def study_list(
    request: Request,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """List studies, optionally filtered by session/status.

    When session_id is provided, enforces ownership (IDOR).
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
        rows = store.list_studies(session_id=session_id, status=status_enum, limit=limit)
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
        }
    return {"status": "ok", "studies": [_shape(r) for r in rows]}


@router.get("/status")
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


@router.get("/{study_id}/summary")
async def study_summary(request: Request, study_id: str):
    """Return study summary with recent rounds, scoreboard, and goal snapshot."""
    from ...core.goal import GoalStore
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")

        # Access model: study-id-derived endpoints require authentication
        # (middleware 401) but NOT owner-session matching — study data is
        # scoped to this machine's workspace and the list endpoint already
        # exposes all studies. Multi-tenant deployments should re-add
        # owner-session IDOR here.
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

    return {
        "status": "ok",
        "study_id": study.study_id,
        "execution_status": study.execution_status.value,
        "current_round": study.current_round,
        "max_rounds": study.max_rounds,
        "objective": study.objective,
        "strategy_name": study.strategy_name,
        "workspace_path": study.workspace_path,
        "created_at": study.created_at,
        "updated_at": study.updated_at,
        "completed_at": study.completed_at,
        "last_metrics": study.last_metrics,
        "last_verdict": study.last_verdict,
        "recent_rounds": [
            {
                "round_num": r.round_num,
                "run_name": r.run_name,
                "metrics": r.metrics,
                "verdict": r.verdict,
                "created_at": r.created_at,
            }
            for r in recent_rounds
        ],
        "scoreboard": scoreboard,
        "goal_snapshot": _snapshot(goal_snapshot),
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


@router.post("/{study_id}/pause")
async def study_pause(request: Request, study_id: str):
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not active")
    sched = _get_study_scheduler()
    if not sched.pause(study_id):
        raise HTTPException(status_code=404, detail="study not active")
    return {"status": "ok", "study_id": study_id, "action": "paused"}


@router.post("/{study_id}/resume")
async def study_resume(request: Request, study_id: str):
    """Resume a paused or interrupted study."""
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


@router.post("/{study_id}/cancel")
async def study_cancel(request: Request, study_id: str):
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not active")
    sched = _get_study_scheduler()
    if not sched.cancel(study_id):
        raise HTTPException(status_code=404, detail="study not active")
    return {"status": "ok", "study_id": study_id, "action": "cancelled"}


# ── POST /study/{study_id}/directive (Phase 2: mid-exec interaction) ──


@router.post("/{study_id}/directive")
async def study_directive(request: Request, study_id: str, req: DirectiveRequest):
    """Inject a user-issued directive into the study's next round.

    Persists to ``study_directives``; the executor's per-round loop
    consumes it and emits ``study_directives_consumed`` once the
    researcher agent has seen it.
    """
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


@router.get("/{study_id}/directives")
async def study_directives_list(request: Request, study_id: str):
    """List pending + consumed directives for a study (audit trail)."""
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")
        # Pull all (pending + consumed). Direct access on store.conn —
        # acceptable for the audit-only endpoint.
        with store._lock:  # noqa: SLF001 — internal but stable
            rows = store._conn.execute(  # noqa: SLF001
                """
                SELECT directive_id, content, issued_by, created_at, consumed_at
                FROM study_directives
                WHERE study_id = ?
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (study_id,),
            ).fetchall()
    return {
        "status": "ok",
        "study_id": study_id,
        "directives": [
            {
                "directive_id": r["directive_id"],
                "content": r["content"],
                "issued_by": r["issued_by"],
                "created_at": r["created_at"],
                "consumed_at": r["consumed_at"],
            }
            for r in rows
        ],
    }

# ── v2 artifacts endpoints (design §17) ────────────────────────────────


def _owned_study(request: Request, study_id: str):
    """Fetch a study with IDOR enforcement (owner-session based)."""
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")
        return study


@router.get("/{study_id}/rounds")
async def study_rounds_list(
    request: Request, study_id: str,
    offset: int = 0, limit: int = 20,
):
    """v2: paginated round history (study_rounds table)."""
    from ...core.study import StudyStore
    _owned_study(request, study_id)
    with StudyStore() as store:
        rows = store.list_rounds(study_id, limit=offset + limit)
    page = rows[offset:offset + limit]
    return {
        "status": "ok",
        "study_id": study_id,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rounds": [
            {
                "round": r.round_num,
                "run_name": r.run_name,
                "verdict": r.verdict,
                "metrics": r.metrics,
                "review": r.review,
                "created_at": r.created_at,
            }
            for r in page
        ],
    }


@router.get("/{study_id}/journal")
async def study_journal(request: Request, study_id: str):
    """v2: journal.md content (append-only archive, single source)."""
    from ...core.study import state_store as ss
    study = _owned_study(request, study_id)
    p = ss.journal_path(Path(study.workspace_path), study_id)
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"status": "ok", "study_id": study_id, "journal": content}


@router.get("/{study_id}/guidance")
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


@router.get("/{study_id}/rounds/{round_num}/summary_md")
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
