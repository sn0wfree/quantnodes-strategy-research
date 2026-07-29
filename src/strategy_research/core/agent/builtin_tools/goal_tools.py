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

from ..tools import BaseTool

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
    """Create or replace a research goal for the current session."""

    name = "create_goal"
    description = (
        "Create a new research goal for the current session. "
        "If a goal already exists, it will be superseded. "
        "Returns the goal_id and status."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID (auto-injected)."},
            "objective": {"type": "string", "description": "Research objective description."},
            "criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of success criteria (optional, uses defaults if omitted).",
            },
        },
        "required": ["objective"],
    }
    repeatable = False

    def execute(self, **kwargs: Any) -> str:
        session_id = _get_session_id(kwargs)
        objective = kwargs.get("objective", "")
        if not objective:
            return _err("missing 'objective'")

        criteria = kwargs.get("criteria")
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except (json.JSONDecodeError, TypeError):
                criteria = [c.strip() for c in criteria.split(",") if c.strip()]

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
                "status": goal.status.value,
                "objective": goal.objective,
                "progress_percent": goal.progress_percent,
            })
        except Exception as exc:
            logger.exception("create_goal failed")
            return _err(f"create_goal failed: {exc}")


# ── 2. AddEvidenceTool ─────────────────────────────────────────────


class AddEvidenceTool(BaseTool):
    """Append evidence to the current research goal."""

    name = "add_evidence"
    description = (
        "Append evidence (analysis results, observations, metrics) to the "
        "current research goal. Optionally link to a specific criterion."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID (auto-injected)."},
            "text": {"type": "string", "description": "Evidence text (required)."},
            "criterion_id": {"type": "string", "description": "Link to a specific criterion (optional)."},
            "source_type": {"type": "string", "description": "Source type (e.g. 'analysis', 'backtest')."},
            "run_id": {"type": "string", "description": "Related run ID (optional)."},
        },
        "required": ["text"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        session_id = _get_session_id(kwargs)
        text = kwargs.get("text", "")
        if not text:
            return _err("missing 'text'")

        criterion_id = kwargs.get("criterion_id")
        source_type = kwargs.get("source_type", "evidence")
        run_id = kwargs.get("run_id")

        try:
            from ...goal import EvidenceInput
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return _err("no active goal for this session; use create_goal first")

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
            return _err(f"add_evidence failed: {exc}")


# ── 3. CompleteGoalTool ────────────────────────────────────────────


class CompleteGoalTool(BaseTool):
    """Mark the current research goal as complete (lite mode)."""

    name = "complete_goal"
    description = (
        "Complete the current research goal. Uses 'lite' mode which verifies "
        "every required criterion has evidence but does not require audit rows. "
        "Optionally include a recap summary."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID (auto-injected)."},
            "recap": {"type": "string", "description": "Optional recap summary of the research."},
        },
        "required": [],
    }
    repeatable = False

    def execute(self, **kwargs: Any) -> str:
        session_id = _get_session_id(kwargs)
        recap = kwargs.get("recap")

        try:
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return _err("no active goal for this session")

            updated = store.complete_lite(
                session_id=session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                recap=recap,
            )
            return _ok({
                "goal_id": updated.goal_id,
                "status": updated.status.value,
                "recap": updated.recap,
            })
        except Exception as exc:
            logger.exception("complete_goal failed")
            return _err(f"complete_goal failed: {exc}")


# ── 4. GetGoalStatusTool ───────────────────────────────────────────


class GetGoalStatusTool(BaseTool):
    """Get the current goal status and progress."""

    name = "get_goal_status"
    description = (
        "Get the current research goal's status, progress, criteria, "
        "and evidence count. Returns a full snapshot."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID (auto-injected)."},
        },
        "required": [],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        session_id = _get_session_id(kwargs)

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
                "status": goal.get("status"),
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
            return _err(f"get_goal_status failed: {exc}")


# ── 5. ListGoalsTool ───────────────────────────────────────────────


class ListGoalsTool(BaseTool):
    """List goals with optional session and status filters."""

    name = "list_goals"
    description = (
        "List research goals. Optionally filter by session_id and/or status. "
        "Returns goal summaries ordered by creation time (newest first)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Filter by session ID (optional)."},
            "status": {"type": "string", "description": "Filter by status (e.g. 'active', 'complete')."},
            "limit": {"type": "integer", "description": "Max results (default 10)."},
        },
        "required": [],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        session_id = kwargs.get("session_id")
        status_str = kwargs.get("status")
        limit = int(kwargs.get("limit", 10))

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
                        "status": g.status.value,
                        "objective": g.objective,
                        "progress_percent": g.progress_percent,
                        "created_at": g.created_at,
                    }
                    for g in goals
                ],
                "count": len(goals),
            })
        except Exception as exc:
            logger.exception("list_goals failed")
            return _err(f"list_goals failed: {exc}")


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
