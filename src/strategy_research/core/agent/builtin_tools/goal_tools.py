"""Goal management tools for the agent.

Tools:
    CreateGoalTool      - create/replace a research goal
    AddEvidenceTool     - append evidence to current goal
    CompleteGoalTool    - mark goal complete (lite mode)
    GetGoalStatusTool   - get current goal snapshot
    ListGoalsTool       - list goals with optional filter
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..tools import BaseTool, EFFECT_DB, ToolContext
from .utils import err_actionable, safe_get_param, try_unwrap_list

logger = logging.getLogger(__name__)


# ── Shared helpers ───────────────────────────────────────────────────


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps(
        {"status": "error", "error": str(message), **extra},
        ensure_ascii=False,
    )


def _get_store():
    """Get a GoalStore instance with default DB path."""
    from ...goal import GoalStore
    return GoalStore()


def _get_session_id(kwargs: dict[str, Any]) -> str:
    """Extract session_id from kwargs (injected by AgentLoop)."""
    sid = kwargs.get("session_id")
    if not sid:
        return "default"
    return str(sid)


# ── 1. CreateGoalTool ──────────────────────────────────────────────


class CreateGoalTool(BaseTool):
    """创建或替换当前会话的研究目标。"""

    name = "create_goal"
    description = "为当前会话创建研究目标 (已存在则取代); 返回 goal_id 与状态。"
    repeatable = False
    strict = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        objective: str,
        criteria: list[str] | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"
        if not objective:
            return err_actionable(
                "missing 'objective'",
                expected="non-empty research objective string, e.g. 'build a momentum strategy for A-shares'",
                fix="pass a non-empty objective describing the research goal",
                tool="create_goal",
            )

        # Defensive: criteria may be string, list, or wrapped list
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except (json.JSONDecodeError, TypeError):
                criteria = [c.strip() for c in criteria.split(",") if c.strip()]
        elif isinstance(criteria, dict):
            # LLM wrapped list in dict (e.g. {"items": [...]})
            unwrapped = try_unwrap_list(criteria)
            if unwrapped is not None:
                criteria = unwrapped

        try:
            from ...goal.context import default_goal_criteria
            store = _get_store()
            goal = store.replace_goal(
                session_id=session_id,
                objective=objective,
                criteria=criteria or default_goal_criteria(),
            )
            return _ok({
                "goal_id": goal.goal_id,
                "goal_status": goal.status.value,
                "objective": goal.objective,
                "progress_percent": goal.progress_percent,
            })
        except Exception as exc:
            logger.exception("create_goal failed")
            return err_actionable(
                f"create_goal failed: {exc}",
                received={"objective": objective, "criteria": criteria},
                fix="verify objective is a non-empty string and criteria is a list of strings",
                tool="create_goal",
            )


# ── 2. AddEvidenceTool ─────────────────────────────────────────────


class AddEvidenceTool(BaseTool):
    """为当前目标添加证据条目。"""

    name = "add_evidence"
    description = "为目标添加证据条目 (指标/观测); 关联可选 criterion/run。"
    repeatable = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        text: str,
        criterion_id: str | None = None,
        source_type: str = "evidence",
        run_id: str | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"
        if not text:
            return err_actionable(
                "missing 'text'",
                expected="non-empty evidence string describing the observation/metric",
                fix="pass a non-empty text, e.g. text='Backtest IC = 0.045 on 2023-12-15'",
                tool="add_evidence",
            )



        try:
            from ...goal import EvidenceInput
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return err_actionable(
                    "no active goal for this session; use create_goal first",
                    fix="call create_goal(objective='...') first to set a research goal",
                    tool="add_evidence",
                )

            evidence = EvidenceInput(
                text=text,
                criterion_id=criterion_id,
                source_type=source_type,
                run_id=run_id,
            )
            record = store.append_evidence(
                session_id=session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                evidence=evidence,
            )

            # Re-fetch to get updated progress
            updated = store.get_current_goal(session_id)
            return _ok({
                "evidence_id": record.evidence_id,
                "goal_id": current.goal_id,
                "progress_percent": updated.progress_percent if updated else 0,
            })
        except Exception as exc:
            logger.exception("add_evidence failed")
            return err_actionable(
                f"add_evidence failed: {exc}",
                received={"text": text[:200] if isinstance(text, str) else text},
                fix="check the error detail and verify the session has an active goal",
                tool="add_evidence",
            )


# ── 3. CompleteGoalTool ────────────────────────────────────────────


class CompleteGoalTool(BaseTool):
    """完成当前目标并附上总结。"""

    name = "complete_goal"
    description = "将当前目标标记为完成, 可附 recap 总结。"
    repeatable = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        recap: str | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"

        try:
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return err_actionable(
                    "no active goal for this session",
                    fix="call create_goal(objective='...') first to set a research goal",
                    tool="complete_goal",
                )

            updated = store.complete_lite(
                session_id=session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                recap=recap,
            )
            return _ok({
                "goal_id": updated.goal_id,
                "goal_status": updated.status.value,
                "recap": updated.recap,
            })
        except Exception as exc:
            logger.exception("complete_goal failed")
            return err_actionable(
                f"complete_goal failed: {exc}",
                fix="check the error detail; the goal may have already been completed",
                tool="complete_goal",
            )


# ── 4. GetGoalStatusTool ───────────────────────────────────────────


class GetGoalStatusTool(BaseTool):
    """查询当前目标状态与证据。"""

    name = "get_goal_status"
    description = "查询当前会话目标: 状态/进度/标准/证据数。"
    repeatable = True
    category = "Goal"
    def execute(
        self,
        ctx: ToolContext,
    ) -> str:
        session_id = ctx.session_id or "default"

        try:
            store = _get_store()
            snapshot = store.get_current_snapshot(session_id)
            if snapshot is None:
                return _ok({
                    "has_goal": False,
                    "message": "no active goal",
                })

            goal = snapshot.get("goal", {})
            criteria = snapshot.get("criteria", [])
            evidence_count = snapshot.get("evidence_count", 0)

            return _ok({
                "has_goal": True,
                "goal_id": goal.get("goal_id"),
                "goal_status": goal.get("status"),
                "objective": goal.get("objective"),
                "progress_percent": goal.get("progress_percent", 0),
                "criteria_count": len(criteria),
                "evidence_count": evidence_count,
                "criteria": [
                    {
                        "criterion_id": c.get("criterion_id"),
                        "text": c.get("text"),
                        "status": c.get("status"),
                        "required": c.get("required", True),
                    }
                    for c in criteria
                ],
            })
        except Exception as exc:
            logger.exception("get_goal_status failed")
            return err_actionable(
                f"get_goal_status failed: {exc}",
                fix="verify the database is accessible and the session_id is valid",
                tool="get_goal_status",
            )


# ── 5. ListGoalsTool ───────────────────────────────────────────────


class ListGoalsTool(BaseTool):
    """列出目标（可按状态过滤）。"""

    name = "list_goals"
    description = "列出目标列表 (可按状态过滤), 返回目标摘要与计数。"
    repeatable = True
    category = "Goal"
    def execute(
        self,
        ctx: ToolContext,
        status: str | None = None,
        limit: int = 10,
    ) -> str:
        session_id = ctx.session_id
        status_str = status

        try:
            from ...goal import GoalStatus, GoalStore
            store = _get_store()
            status_filter = GoalStatus(status_str) if status_str else None
            goals = store.list_goals(
                session_id=session_id,
                status=status_filter,
                limit=limit,
            )
            return _ok({
                "goals": [
                    {
                        "goal_id": g.goal_id,
                        "session_id": g.session_id,
                        "goal_status": g.status.value,
                        "objective": g.objective,
                        "progress_percent": g.progress_percent,
                        "created_at": g.created_at,
                    }
                    for g in goals
                ],
                "count": len(goals),
            })
        except ValueError as exc:
            # GoalStatus enum parsing failed
            return err_actionable(
                f"invalid status value: {status_str!r}",
                received=status_str,
                expected="one of: active, complete, abandoned (or omit for all)",
                fix="either omit `status` to list all, or pass a valid status like 'active'",
                tool="list_goals",
            )
        except Exception as exc:
            logger.exception("list_goals failed")
            return err_actionable(
                f"list_goals failed: {exc}",
                fix="check the database connection and parameter values",
                tool="list_goals",
            )


# ── Registration ────────────────────────────────────────────────────


def register_goal_tools(registry) -> None:
    """Register all goal tools with the given ToolRegistry."""
    registry.register(CreateGoalTool())
    registry.register(AddEvidenceTool())
    registry.register(CompleteGoalTool())
    registry.register(GetGoalStatusTool())
    registry.register(ListGoalsTool())


__all__ = [
    "CreateGoalTool",
    "AddEvidenceTool",
    "CompleteGoalTool",
    "GetGoalStatusTool",
    "ListGoalsTool",
    "register_goal_tools",
]
