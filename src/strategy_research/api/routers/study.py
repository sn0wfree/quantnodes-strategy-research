"""Study API router — ``/api/study/*``.

Exposes the study task system: create a study (which creates a goal
ledger row + queues the autoresearch executor), inspect status, and
pause / resume / cancel. See ``docs/study-longhorizon-plan.md``.

The scheduler is lazily wired to ``SessionService`` so the chat/study
mutex (``is_session_processing`` / ``mark_session_processing``) works
out of the box.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ._task_utils import log_task_exception

router = APIRouter()
logger = logging.getLogger(__name__)


def _create_minimal_strategy(strat_dir: Path, strategy_name: str) -> None:
    """Create minimal strategy.py for new study."""
    strategy_py = strat_dir / "strategy.py"
    if not strategy_py.exists():
        strategy_py.write_text(
            f'"""Auto-generated strategy: {strategy_name}"""\n'
            f"# This file will be overwritten by the autoresearch agent.\n\n"
            f"PARAMS = {{}}\n"
            f"FACTOR_EXPRS = []\n"
            f"FACTOR_WEIGHT_METHOD = \"equal\"\n",
            encoding="utf-8",
        )


_RESULTS_TSV_HEADER = (
    "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\t"
    "ann_return\tturnover\tfactors_added\tfactors_removed\t"
    "params_changed\tstatus\tdescription\tround\n"
)

_TODOS_TEMPLATE = (
    "# 任务子任务清单（评审维护）\n"
    "\n"
    "## 待办\n"
    "\n"
    "## 进行中\n"
    "\n"
    "## 已放弃\n"
    "\n"
)

_KNOWLEDGE_TEMPLATE = (
    "# 知识储备与 Idea 池\n"
    "<!-- 外部信息收集沉淀 · 追加式 · 每轮注入近期条目 -->\n"
    "\n"
)

_STUDY_GUIDANCE_TEMPLATE = """\
# 研究指引（每轮自动注入）

## 决策规则（人类判断点）
<!-- 在此定义硬性规则；enforce:true 的 gate 走 frontmatter -->

## 偏好
- 因子表达式保持可解释性；优先验证与任务目标直接相关的因子。

## 任务文档说明（需要时用 read_file 按需读取）
- `study/{study_id}/journal.md`：全任务轮次归档（追加式，一行式摘要）。
- `study/{study_id}/rounds/round_NNNN/summary.md`：单轮详细总结。
- `study/{study_id}/todos.md`：任务子任务清单。
- `study/{study_id}/knowledge.md`：外部信息储备。
- 每轮产物默认已注入上下文，仅在信息不足时按需读取。
"""


def _init_study_dir(
    ws: Path,
    study_id: str,
    strategy_name: str,
    objective: str,
    guidance_md: str | None = None,
) -> dict:
    """v2 bootstrap: autonomous study directory (design §6.2).

    Creates baseline/strategy.py, results.tsv header, guidance.md,
    todos.md, knowledge.md and state.json.
    """
    from ...core.study.state_store import init as init_state

    root = ws / "study" / study_id
    (root / "baseline").mkdir(parents=True, exist_ok=True)
    (root / "rounds").mkdir(parents=True, exist_ok=True)

    # baseline strategy: existing strategy copied, else minimal template
    baseline_py = root / "baseline" / "strategy.py"
    src = ws / "strategies" / strategy_name / "strategy.py"
    if src.exists():
        baseline_py.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        _create_minimal_strategy(root / "baseline", strategy_name)

    # results.tsv header (round column last)
    tsv = root / "results.tsv"
    if not tsv.exists():
        tsv.write_text(_RESULTS_TSV_HEADER, encoding="utf-8")

    # guidance.md: request override > built-in template
    guidance = root / "guidance.md"
    if guidance_md:
        guidance.write_text(guidance_md, encoding="utf-8")
    elif not guidance.exists():
        guidance.write_text(
            _STUDY_GUIDANCE_TEMPLATE.format(study_id=study_id),
            encoding="utf-8",
        )

    # todos.md / knowledge.md templates
    todos = root / "todos.md"
    if not todos.exists():
        todos.write_text(_TODOS_TEMPLATE, encoding="utf-8")
    knowledge = root / "knowledge.md"
    if not knowledge.exists():
        knowledge.write_text(_KNOWLEDGE_TEMPLATE, encoding="utf-8")

    # state.json
    init_state(ws, study_id)

    return {"root": str(root), "results_tsv": str(tsv)}


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
    from .chat import _get_session_service
    from ...core.study import StudyScheduler, StudyStore

    db_path = str(StudyStore().db_path)
    sched = _scheduler_cache.get(db_path)
    if sched is None:
        store = StudyStore(db_path=db_path)
        svc = _get_session_service()
        sched = StudyScheduler(store, session_service=svc)
        _scheduler_cache[db_path] = sched
        logger.info("study scheduler instantiated for db=%s", db_path)
    return sched


def _warm_study_scheduler_for_backend(sched: "StudyScheduler") -> None:
    """Back-door wiring for `_handle_study_command` + lifespan startup.

    The chat slash command needs a scheduler bound to the FastAPI event
    loop BEFORE the scheduler could lazily create its consumer tasks on
    a previous, torn-down loop. Callers must invoke ``_get_study_scheduler``
    again on a live loop to refresh locally, but startup warming ensures
    the loop a single _consumer lives on matches the final loop.
    """


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
    # Workspace validation — fail fast on bad config before persistence.
    ws = Path(req.workspace_path)
    if not ws.exists():
        raise HTTPException(
            status_code=400,
            detail=f"workspace_path does not exist: {req.workspace_path}",
        )
    # Security: reject strategy_name that escapes the workspace via "..",
    # path separators, or NUL bytes. Then resolve and verify containment.
    if not req.strategy_name or "/" in req.strategy_name or "\\" in req.strategy_name \
            or "\0" in req.strategy_name or req.strategy_name.startswith("."):
        raise HTTPException(
            status_code=400,
            detail="strategy_name must be a single segment without path separators",
        )
    ws_resolved = ws.resolve()
    strat_dir = (ws_resolved / "strategies" / req.strategy_name).resolve()
    try:
        strat_dir.relative_to(ws_resolved)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="strategy_name resolves outside workspace",
        )
    if not strat_dir.exists():
        # Auto-create strategy directory with minimal strategy.py
        strat_dir.mkdir(parents=True, exist_ok=True)
        _create_minimal_strategy(strat_dir, req.strategy_name)

    try:
        from ...core.goal import GoalStore
        from ...core.goal.context import default_goal_criteria
        from ...core.study import StudyStore, StudyStatus, default_metric_targets

        targets = (
            [t.model_dump() for t in req.metric_targets]
            if req.metric_targets else default_metric_targets()
        )
        # v2 single identity: the study row is created first so its
        # session_id (== study_id) can be the goal's isolation domain.
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
            # Goal ledger under the study's own identity: parallel studies
            # never supersede each other's goals (supersede=False).
            goal_store = GoalStore()
            goal = goal_store.replace_goal(
                session_id=study.study_id,
                objective=req.objective,
                criteria=default_goal_criteria(),
                supersede=False,
            )
            study = store.update_goal_id(study.study_id, goal.goal_id)

        # v2 bootstrap: autonomous study directory (baseline strategy,
        # results.tsv, guidance.md, todos.md, knowledge.md, state.json)
        if req.executor_type == "autoresearch":
            _init_study_dir(
                ws_resolved,
                study.study_id,
                req.strategy_name,
                req.objective,
                guidance_md=req.guidance_md,
            )

        # Phase 3: Start GoalWorkflowRunner instead of scheduler
        # AEGIS: executor_type="autoresearch" uses AutoresearchRunner (round-based)
        # executor_type="workflow" uses GoalWorkflowRunner (single DAG)
        if req.executor_type == "autoresearch":
            # Use scheduler → AutoresearchRunner (AEGIS-powered round loop)
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
        else:
            # Original GoalWorkflowRunner path (single DAG execution)
            from ...core.goal.workflow import (
                GoalWorkflowConfig, GoalWorkflowGoalConfig, GoalAgentConfig,
                CompletionConfig, GoalWorkflowRunner,
            )
            from .chat import _get_session_service

            agent_configs = [
            GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                           tools=["read_file", "list_history", "factor_analysis", "web_search",
                                  "read_url", "get_market_data", "search_symbol"],
                           input_from=[], evidence_criterion=0, timeout=180, max_retries=3),
            GoalAgentConfig(id="data_quality", prompt_file=".prompts/data_quality.md",
                           tools=["read_file", "web_search", "read_url", "get_market_data",
                                  "list_data_sources"],
                           input_from=["researcher"], evidence_criterion=1, timeout=120, max_retries=2),
            GoalAgentConfig(id="factor_analyst", prompt_file=".prompts/factor_analyst.md",
                           tools=["read_file", "compute_factor", "factor_analysis", "get_market_data"],
                           input_from=["researcher", "data_quality"], evidence_criterion=1,
                           timeout=180, max_retries=3),
            GoalAgentConfig(id="strategist", prompt_file=".prompts/strategist.md",
                           tools=["read_file", "write_file", "run_backtest", "git_diff",
                                  "web_search", "read_url", "get_market_data"],
                           input_from=["researcher", "data_quality", "factor_analyst"],
                           evidence_criterion=2, timeout=240, max_retries=3),
            GoalAgentConfig(id="portfolio_construction", prompt_file=".prompts/portfolio_construction.md",
                           tools=["read_file", "get_market_data"],
                           input_from=["strategist"], evidence_criterion=2, timeout=120, max_retries=2),
            GoalAgentConfig(id="backtest", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=[], input_from=["portfolio_construction"], evidence_criterion=2,
                           timeout=300, max_retries=1, executor_type="python_executor",
                           python_function="run_backtest_script"),
            GoalAgentConfig(id="risk_controller", prompt_file=".prompts/risk_controller.md",
                           tools=["read_file", "factor_analysis", "get_market_data"],
                           input_from=["backtest"], evidence_criterion=3, timeout=180, max_retries=2),
            GoalAgentConfig(id="attribution_analyst", prompt_file=".prompts/attribution_analyst.md",
                           tools=["read_file", "factor_analysis"],
                           input_from=["backtest", "risk_controller"], evidence_criterion=3,
                           timeout=180, max_retries=2),
            GoalAgentConfig(id="anti_overfit_analyst", prompt_file=".prompts/anti_overfit_analyst.md",
                           tools=["read_file", "list_history", "factor_analysis"],
                           input_from=["backtest", "risk_controller", "attribution_analyst"],
                           evidence_criterion=4, timeout=180, max_retries=2),
            GoalAgentConfig(id="backtest_diagnostics", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=["read_file", "run_backtest", "git_diff"],
                           input_from=["anti_overfit_analyst"], evidence_criterion=4,
                           timeout=120, max_retries=2),
            GoalAgentConfig(id="decide", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=[], input_from=["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
                           evidence_criterion=4, timeout=60, max_retries=1,
                           executor_type="evaluator", python_function="decide"),
        ]

        config = GoalWorkflowConfig(
            name=f"autoresearch_{req.strategy_name}",
            description=f"9-agent autoresearch: {req.objective}",
            goal=GoalWorkflowGoalConfig(
                default_criteria=default_goal_criteria(),
                risk_tier="research_general",
            ),
            agents=agent_configs,
            dag={
                "researcher": [],
                "data_quality": ["researcher"],
                "factor_analyst": ["researcher", "data_quality"],
                "strategist": ["researcher", "data_quality", "factor_analyst"],
                "portfolio_construction": ["strategist"],
                "backtest": ["portfolio_construction"],
                "risk_controller": ["backtest"],
                "attribution_analyst": ["backtest", "risk_controller"],
                "anti_overfit_analyst": ["backtest", "risk_controller", "attribution_analyst"],
                "backtest_diagnostics": ["anti_overfit_analyst"],
                "decide": ["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
            },
            completion=CompletionConfig(
                mode="auto",
                metric_targets=targets,
                monitor_interval_seconds=req.monitor_interval_seconds,
            ),
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
    from ...core.study import StudyStore, StudyStatus
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
        # Security: enforce session ownership (study -> session lookup).
        _verify_study_ownership(request, study.owner_session_id)

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


def _verify_study_ownership(request: Request, session_id: str) -> None:
    """Enforce IDOR for any study-derived endpoint (study_id → owner session)."""
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), session_id, user_id)


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
    _verify_study_ownership(request, sid)
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

    # Security: enforce session ownership.
    _verify_study_ownership(request, sid)
    sched = _get_study_scheduler()

    # Check current status to decide resume path
    from ...core.study import StudyStore, StudyStatus
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
    _verify_study_ownership(request, sid)
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
    # Security: enforce session ownership before any DB mutation.
    sid = _study_session_id(study_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="study not found")
    _verify_study_ownership(request, sid)

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
        # Security: enforce session ownership.
        _verify_study_ownership(request, study.owner_session_id)
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
        _verify_study_ownership(request, study.owner_session_id)
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
    from ...core.study import state_store as ss
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
