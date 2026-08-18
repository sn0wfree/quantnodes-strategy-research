"""Study bootstrap — shared creation orchestration.

Centralizes the "create a study and queue it" flow so the HTTP router
(``api/routers/study.py``) and the scheduled-research dispatch bridge use
identical validation + persistence semantics.

Flow: workspace/strategy validation → ``StudyStore.create_study`` →
goal ledger under the study's own identity (``supersede=False``) →
v2 autonomous study directory → ``StudyScheduler.submit``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import StudyStatus
from .state_store import init as init_state

if TYPE_CHECKING:
    from .models import StudyRecord

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


def validate_workspace_strategy(
    workspace_path: str | Path, strategy_name: str
) -> Path:
    """Validate workspace + strategy name and bootstrap the strategy dir.

    Raises ``ValueError`` with a user-facing message when:
    - workspace does not exist
    - strategy_name is not a single segment (path traversal / NUL guard)
    - strategy_name resolves outside the workspace

    On success the resolved workspace is returned; the strategy directory
    is auto-created with a minimal ``strategy.py`` when missing.
    """
    ws = Path(workspace_path)
    if not ws.exists():
        raise ValueError(f"workspace_path does not exist: {workspace_path}")
    if not strategy_name or "/" in strategy_name or "\\" in strategy_name \
            or "\0" in strategy_name or strategy_name.startswith("."):
        raise ValueError(
            "strategy_name must be a single segment without path separators"
        )
    ws_resolved = ws.resolve()
    strat_dir = (ws_resolved / "strategies" / strategy_name).resolve()
    try:
        strat_dir.relative_to(ws_resolved)
    except ValueError:
        raise ValueError("strategy_name resolves outside workspace")
    if not strat_dir.exists():
        strat_dir.mkdir(parents=True, exist_ok=True)
        _create_minimal_strategy(strat_dir, strategy_name)
    return ws_resolved


def init_study_dir(
    ws: Path,
    study_id: str,
    strategy_name: str,
    objective: str,
    guidance_md: str | None = None,
    graph: "StudyGraph | None" = None,
    auto_compose: bool = False,
) -> dict:
    """v2 bootstrap: autonomous study directory (design §6.2).

    Creates baseline/strategy.py, results.tsv header, guidance.md,
    todos.md, knowledge.md, state.json and graph.json.

    ``graph`` (optional): custom execution graph (multi-entry/exit).
    When None and ``auto_compose=False``, ``DEFAULT_STANDARD_GRAPH`` is
    used. When ``auto_compose=True``, :class:`DAGPlanner` is invoked
    and its output written to ``graph.json``; failure falls back to
    the standard 8-node template.
    """
    root = ws / "study" / study_id
    (root / "baseline").mkdir(parents=True, exist_ok=True)
    (root / "rounds").mkdir(parents=True, exist_ok=True)

    baseline_py = root / "baseline" / "strategy.py"
    src = ws / "strategies" / strategy_name / "strategy.py"
    if src.exists():
        baseline_py.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        _create_minimal_strategy(root / "baseline", strategy_name)

    tsv = root / "results.tsv"
    if not tsv.exists():
        tsv.write_text(_RESULTS_TSV_HEADER, encoding="utf-8")

    guidance = root / "guidance.md"
    if guidance_md:
        guidance.write_text(guidance_md, encoding="utf-8")
    elif not guidance.exists():
        guidance.write_text(
            _STUDY_GUIDANCE_TEMPLATE.format(study_id=study_id),
            encoding="utf-8",
        )

    todos = root / "todos.md"
    if not todos.exists():
        todos.write_text(_TODOS_TEMPLATE, encoding="utf-8")
    knowledge = root / "knowledge.md"
    if not knowledge.exists():
        knowledge.write_text(_KNOWLEDGE_TEMPLATE, encoding="utf-8")

    # Execution graph (multi-entry/exit). Persisted so the runner can
    # topologically schedule agents each round. Falls back to the
    # standard 8-node template when no custom graph is supplied.
    from .graph import StudyGraph
    from .graph_templates import DEFAULT_STANDARD_GRAPH

    graph_path = root / "graph.json"
    if not graph_path.exists():
        graph_to_write = graph if graph is not None else DEFAULT_STANDARD_GRAPH
        if graph is None and auto_compose:
            try:
                from .dag_planner import DAGPlanner
                plan = DAGPlanner().plan(objective)
                graph_to_write = plan.config.to_study_graph()
                logger.info(
                    "auto_compose: selected %s for objective %r",
                    plan.selected_agents, objective[:40],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_compose failed (%s); falling back to standard graph",
                    exc,
                )
                graph_to_write = DEFAULT_STANDARD_GRAPH
        graph_path.write_text(
            graph_to_write.to_json(),
            encoding="utf-8",
        )

    init_state(ws, study_id)

    return {"root": str(root), "results_tsv": str(tsv)}


def create_study_record(
    *,
    owner_session_id: str,
    objective: str,
    workspace_path: str | Path,
    strategy_name: str,
    metric_targets: list[dict] | None = None,
    budget_token: int | None = None,
    budget_turn: int | None = None,
    budget_time_seconds: int | None = None,
    cooldown_base: float = 30.0,
    cooldown_jitter: float = 10.0,
    min_cooldown: float = 1.0,
    max_rounds: int | None = None,
    early_stop_patience: int = 3,
    behavior: str | None = None,
    monitor_interval_seconds: int | None = None,
    guidance_md: str | None = None,
    lazy_detection_interval: int = 10,
    keep_recent: int = 10,
    auto_compose_graph: bool = False,
    selected_agents: list[str] | None = None,
    graph_override: "StudyGraph | None" = None,
) -> "StudyRecord":
    """Create a study (validation + ledger + autonomous dir), not queued.

    Sync: safe to call from the API route / dispatch bridge; returns the
    persisted ``StudyRecord`` for the caller to submit via
    ``StudyScheduler.submit``.
    """
    from .models import default_metric_targets
    from .store import StudyStore

    ws = validate_workspace_strategy(workspace_path, strategy_name)
    targets = metric_targets if metric_targets is not None else default_metric_targets()

    # v2 single identity: the study row is created first so its
    # session_id (== study_id) can be the goal's isolation domain.
    with StudyStore() as store:
        study = store.create_study(
            owner_session_id=owner_session_id,
            goal_id=None,
            objective=objective,
            workspace_path=str(workspace_path),
            strategy_name=strategy_name,
            metric_targets=targets,
            budget_token=budget_token,
            budget_turn=budget_turn,
            budget_time_seconds=budget_time_seconds,
            cooldown_base=cooldown_base,
            cooldown_jitter=cooldown_jitter,
            min_cooldown=min_cooldown,
            max_rounds=max_rounds,
            early_stop_patience=early_stop_patience,
            behavior=behavior,
            monitor_interval_seconds=monitor_interval_seconds,
            lazy_detection_interval=lazy_detection_interval,
            keep_recent=keep_recent,
        )
        # Goal ledger under the study's own identity: parallel studies
        # never supersede each other's goals (supersede=False).
        from ..goal import GoalStore
        from ..goal.context import default_goal_criteria

        goal_store = GoalStore()
        goal = goal_store.replace_goal(
            session_id=study.study_id,
            objective=objective,
            criteria=default_goal_criteria(),
            supersede=False,
        )
        study = store.update_goal_id(study.study_id, goal.goal_id)

        # Record guidance_md as the startup directive so it appears in
        # the "指令记录" audit trail.
        if guidance_md and guidance_md.strip():
            store.add_directive(
                study.study_id,
                guidance_md.strip(),
                issued_by="system",
            )

    init_study_dir(
        ws, study.study_id, strategy_name, objective,
        guidance_md=guidance_md,
        graph=graph_override,
        auto_compose=auto_compose_graph,
    )
    return study


async def create_and_queue_study(
    *,
    owner_session_id: str,
    objective: str,
    workspace_path: str | Path,
    strategy_name: str,
    metric_targets: list[dict] | None = None,
    budget_token: int | None = None,
    budget_turn: int | None = None,
    budget_time_seconds: int | None = None,
    cooldown_base: float = 30.0,
    cooldown_jitter: float = 10.0,
    min_cooldown: float = 1.0,
    max_rounds: int | None = None,
    behavior: str | None = None,
    monitor_interval_seconds: int | None = None,
    guidance_md: str | None = None,
    lazy_detection_interval: int = 10,
    keep_recent: int = 10,
    scheduler: Any | None = None,
) -> dict:
    """Create a study (ledger + autonomous dir) and queue it for execution.

    Returns an API-compatible dict: ``{status, study_id, goal_id,
    session_id, execution_status, executor_type}``.

    When ``scheduler`` is given the study is submitted onto it; otherwise
    the study is persisted in ``QUEUED`` and the caller must submit it.
    """
    study = create_study_record(
        owner_session_id=owner_session_id,
        objective=objective,
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        metric_targets=metric_targets,
        budget_token=budget_token,
        budget_turn=budget_turn,
        budget_time_seconds=budget_time_seconds,
        cooldown_base=cooldown_base,
        cooldown_jitter=cooldown_jitter,
        min_cooldown=min_cooldown,
        max_rounds=max_rounds,
        behavior=behavior,
        monitor_interval_seconds=monitor_interval_seconds,
        guidance_md=guidance_md,
        lazy_detection_interval=lazy_detection_interval,
        keep_recent=keep_recent,
    )
    if scheduler is not None:
        await scheduler.submit(study)
    return {
        "status": "ok",
        "study_id": study.study_id,
        "goal_id": study.goal_id,
        "session_id": study.study_id,
        "execution_status": StudyStatus.QUEUED.value,
        "executor_type": "autoresearch",
    }
