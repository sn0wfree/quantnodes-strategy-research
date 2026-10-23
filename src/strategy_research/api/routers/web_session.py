"""Web session API — create/list/update/delete + messages + FTS5 search.

Sessions and messages are persisted to SQLite (same DB as users).

Schema:
  sessions:       id, user_id, title, created_at, updated_at,
                  starred, tags_json, message_count, archived
  messages:       id, session_id (FK CASCADE), role, content,
                  parts_json, created_at, metadata_json
  messages_fts:   FTS5 virtual table over messages(content, role)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSessionCreate(BaseModel):
    title: str = "新会话"
    id: Optional[str] = None  # explicit id (DAG-orchestrator sessions: "dag:{name}")


class WebSessionUpdate(BaseModel):
    title: Optional[str] = None
    starred: Optional[bool] = None
    archived: Optional[bool] = None
    tags: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────


from ..session.web_session_db import (  # noqa: E402,F401  (re-exports for old import paths)
    _add_column,
    _drop_column,
    _ensure_fts_table,
    _ensure_fts_table_impl,
    _ensure_schema,
    _fetch_session_owned,
    _get_db,
    _get_db_path,  # noqa: F401  (re-export: tests patch this name)
    _has_column,
    _migrate_message_types,
    _migrate_text_part_ids,
    _row_to_message,
    _row_to_session,
    _run_schema_migrations,
    _validate_identifier,
)


def persist_message(
    session_id: str,
    role: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
    metadata: Optional[dict[str, Any]] = None,
    message_id: Optional[str] = None,
    created_at: Optional[float] = None,
    tool_call_id: Optional[str] = None,
    message_type: str = "assistant",
    seq: int | None = None,
) -> str:
    """Insert a message and bump session counters. Returns message id.

    Safe to call from background tasks — does not raise on failure (logs only).

    Args:
        message_type: One of 'user' | 'assistant' | 'tool' | 'compaction' | 'error'.
            Defaults to 'assistant' for backward compat.
        seq: Per-session monotonic sequence number (Level 1, opencode-aligned).
            If None, falls back to 0 (the column default). Callers SHOULD
            pass an explicit seq via the SeqGenerator for new messages.
    """
    logger.debug("[DB] persist_message session=%s role=%s type=%s content_len=%d seq=%s",
                session_id, role, message_type, len(content), seq)
    msg_id = message_id or str(uuid.uuid4())
    ts = created_at or time.time()
    parts_json = json.dumps(parts, ensure_ascii=False) if parts is not None else None
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            logger.warning("[DB] persist_message: session %s not found", session_id)
            return msg_id

        # Level 2 / Phase 2 commit 6: parts_json and tool_call_id
        # columns were dropped. Build the INSERT dynamically based on
        # which columns are present, so this function works on both
        # pre- and post-migration DBs during the cleanup window.
        has_parts_json = _has_column(conn, "messages", "parts_json")
        has_tool_call_id = _has_column(conn, "messages", "tool_call_id")

        cols = ["id", "session_id", "role", "content", "created_at", "metadata_json", "message_type", "seq"]
        placeholders = ["?", "?", "?", "?", "?", "?", "?", "?"]
        vals: list[Any] = [msg_id, session_id, role, content, ts, metadata_json, message_type, seq or 0]
        if has_parts_json:
            cols.append("parts_json")
            placeholders.append("?")
            vals.append(parts_json)
        if has_tool_call_id:
            cols.append("tool_call_id")
            placeholders.append("?")
            vals.append(tool_call_id)

        sql = (
            f"INSERT INTO messages ({','.join(cols)}) "
            f"VALUES ({','.join(placeholders)})"
        )
        conn.execute(sql, vals)
        logger.debug("[DB] persisted id=%s", msg_id)

        # Dual-write: also insert parts into message_parts (Level 2, Phase 2
        # commit 2). The old parts_json column remains the source of truth
        # until commit 5 switches the read path. This commit only adds
        # the parallel write so commits 3-4 (migration + read switch) can
        # proceed without losing data.
        if parts:
            for i, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                part_id = str(uuid.uuid4())
                part_type = p.get("type", "text")
                part_data_json = json.dumps(p, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO message_parts "
                    "(id, message_id, session_id, type, data_json, seq, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (part_id, msg_id, session_id, part_type, part_data_json, i, ts),
                )

        conn.execute(
            "UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("persist_message failed for session %s: %s", session_id, exc, exc_info=True)
    return msg_id


def auto_title_session(session_id: str, content: str, max_len: int = 30) -> Optional[str]:
    """Set session title from first user message if title is still default.

    Returns the new title if changed, None otherwise.
    """
    if not content or not content.strip():
        return None
    title = content.strip().replace("\n", " ")
    if len(title) > max_len:
        title = title[:max_len].rstrip() + "…"
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT title, message_count FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        if row["title"] != "新会话":
            return None
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), session_id),
        )
        conn.commit()
        return title
    except Exception as exc:
        logger.error("auto_title_session failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Session CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("")
async def create_session(body: WebSessionCreate, request: Request):
    """Create a new web session.

    ``body.id`` is optional: DAG-orchestrator sessions pass an explicit
    ``dag:{name}`` id (bound to the definition); normal sessions leave it
    unset and get a server-generated uuid. If the id already exists, the
    existing session is returned unchanged (idempotent upsert).
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    session_id = body.id or str(uuid.uuid4())
    now = time.time()
    conn = _get_db()
    existing = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if existing is not None:
        if existing["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return _row_to_session(existing)
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
        "starred, tags_json, message_count, archived) "
        "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
        (session_id, user_id, body.title, now, now),
    )
    conn.commit()
    return {
        "id": session_id,
        "user_id": user_id,
        "title": body.title,
        "created_at": now,
        "updated_at": now,
        "starred": False,
        "tags": [],
        "message_count": 0,
        "archived": False,
    }


@router.get("")
async def list_sessions(request: Request, limit: int = 50, include_archived: bool = False):
    """List sessions for current user, most recent first."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    sql = (
        "SELECT * FROM sessions WHERE user_id = ? "
        + ("" if include_archived else "AND archived = 0 ")
        + "ORDER BY updated_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, (user_id, limit)).fetchall()
    return {"sessions": [_row_to_session(r) for r in rows]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get a single session."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
    return _row_to_session(row)


@router.patch("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session metadata: title, starred, archived, tags."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
    now = time.time()
    fields: dict[str, Any] = {"updated_at": now}
    if body.title is not None:
        fields["title"] = body.title
    if body.starred is not None:
        fields["starred"] = 1 if body.starred else 0
    if body.archived is not None:
        fields["archived"] = 1 if body.archived else 0
    if body.tags is not None:
        fields["tags_json"] = json.dumps(body.tags, ensure_ascii=False)

    if len(fields) > 1:  # has at least one user-supplied change
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [session_id]
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", params)
        conn.commit()

    # Re-read
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row)


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Soft-delete a session (mark as archived).

    Messages and event_log are preserved so the projector can continue
    flushing and SSE stays functional for active studies.  The session
    disappears from ``list_sessions`` (which filters ``archived=0``)
    but remains readable via ``GET /{session_id}``.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
    conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?", (session_id,))
    conn.commit()
    _invalidate_projection(session_id, request)
    return {"status": "ok", "deleted_id": session_id}


def _invalidate_projection(session_id: str, request: Request | None = None) -> None:
    """Drop the projector's in-memory state for a deleted session.

    The projector cache lives on the EventStore held by the process-wide
    SessionService. Prefers the DI container (attached by ``create_app``);
    falls back to the process-wide service cache for tests/scripts.
    """
    try:
        if request is not None:
            container = getattr(request.app.state, "_container", None)
            if container is not None:
                bus = container.session_service.event_bus
                invalidate = getattr(bus, "invalidate", None)
                if callable(invalidate):
                    invalidate(session_id)
                return
        from .chat import _session_service_cache
        for service in _session_service_cache.values():
            bus = getattr(service, "event_bus", None)
            invalidate = getattr(bus, "invalidate", None)
            if callable(invalidate):
                invalidate(session_id)
    except Exception as exc:
        logger.debug("projection invalidation skipped: %s", exc)


def delete_messages(session_id: str, message_ids: list[str]) -> None:
    """Delete specific messages by ID from a session."""
    if not message_ids:
        return
    try:
        conn = _get_db()
        placeholders = ",".join("?" for _ in message_ids)
        cur = conn.execute(
            f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
            [session_id, *message_ids],
        )
        # Only decrement by the number of rows actually deleted, so
        # message_count can never go negative when some ids don't exist.
        deleted = cur.rowcount
        conn.execute(
            "UPDATE sessions SET message_count = message_count - ?, updated_at = ? WHERE id = ?",
            (deleted, time.time(), session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("delete_messages failed for session %s: %s", session_id, exc)



# ─────────────────────────────────────────────────────────────────────────────
# Messages endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 200,
    before: Optional[float] = None,
):
    """List messages for a session, oldest first.

    Optional ``before`` cursor (unix ts) for pagination. ``has_more=True`` if
    there are additional older messages beyond ``limit``.

    Order key: ``seq`` (Level 1, opencode-aligned). Per-session monotonic
    counter that's invariant under clock skew. ``before`` continues to
    accept a unix ts; we map it to the maximum seq seen before that ts
    via a subquery for cursor-pagination compatibility.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
    params: list[Any] = [session_id]
    cursor_sql = ""
    if before is not None:
        # Convert unix-ts cursor into a seq cursor by finding the
        # smallest seq with created_at > before. This preserves the
        # external API while using seq internally for ordering.
        cursor_sql = (
            "AND seq > COALESCE("
            "(SELECT MIN(seq) - 1 FROM messages "
            "WHERE session_id = ? AND created_at >= ?), 0)"
        )
        params.extend([session_id, before])
    params.append(limit + 1)  # +1 to detect has_more
    rows = conn.execute(
        f"SELECT * FROM messages WHERE session_id = ? {cursor_sql} "
        "ORDER BY seq ASC LIMIT ?",
        params,
    ).fetchall()
    has_more = len(rows) > limit

    # Batch-fetch parts for all messages in one query (avoids N+1).
    # Level 2 / Phase 2 commit 4: parts are now in the message_parts
    # table. Read from there; fall back to parts_json only if no
    # message_parts rows exist (pre-migration rows).
    if rows:
        message_ids = [r["id"] for r in rows[:limit]]
        placeholders = ",".join("?" * len(message_ids))
        parts_rows = conn.execute(
            f"SELECT message_id, data_json FROM message_parts "
            f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
            message_ids,
        ).fetchall()
        # Group by message_id, preserving seq order. Only include
        # messages that have at least one message_parts row; missing
        # keys (via .get) signal to _row_to_message that the caller
        # didn't pre-fetch (so it falls back to parts_json).
        parts_by_msg: dict[str, list[Any]] = {}
        for mid, data_json in parts_rows:
            try:
                part = json.loads(data_json)
                if isinstance(part, dict):
                    parts_by_msg.setdefault(mid, []).append(part)
            except (json.JSONDecodeError, TypeError):
                pass
    else:
        parts_by_msg = {}
    messages = [
        _row_to_message(r, parts=parts_by_msg.get(r["id"]))
        for r in rows[:limit]
    ]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    return {"messages": messages, "has_more": has_more, "total": total}


# ─────────────────────────────────────────────────────────────────────────────
# Session state (B13): backfill agent / DAG / goal panels after reload.
# ─────────────────────────────────────────────────────────────────────────────


def _build_goal_snapshot(request: Request, session_id: str) -> dict | None:
    """Return the current goal snapshot dict for a session, or None.

    Pulls goal + criteria + evidence_count from GoalStore. The frontend
    Goal store expects a specific shape — the mapping in
    ``_shape_goal_for_frontend`` adapts the snapshot fields for that
    contract.
    """
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        with GoalStore(db_path=db_path) as store:
            return store.get_current_snapshot(session_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] goal snapshot failed: %s", e)
        return None


def _shape_goal_for_frontend(snapshot: dict | None) -> dict | None:
    """Map a GoalStore snapshot to the frontend Goal store shape.

    Frontend fields (see webui stores/goal.ts):
      goal_id, session_id, status, objective, progress_percent,
      criteria[{criterion_id, text, status, evidence_count}],
      evidence_count
    """
    if not snapshot:
        return None
    goal = snapshot.get("goal") or {}
    criteria = snapshot.get("criteria") or []
    return {
        "goal_id": goal.get("goal_id"),
        "session_id": goal.get("session_id", ""),
        "status": goal.get("status", "active"),
        "objective": goal.get("objective", ""),
        "progress_percent": _compute_goal_progress_percent(criteria),
        "criteria": [
            {
                "criterion_id": c.get("criterion_id", ""),
                "text": c.get("text", ""),
                "status": c.get("status", "pending"),
                "evidence_count": c.get("evidence_count", 0),
            }
            for c in criteria
        ],
        "evidence_count": snapshot.get("evidence_count", 0),
        "recap": goal.get("recap"),
    }


def _compute_goal_progress_percent(criteria: list[dict]) -> int:
    """0..100 — share of criteria with status 'covered' or 'complete'."""
    if not criteria:
        return 0
    done = sum(1 for c in criteria if c.get("status") in ("covered", "complete"))
    return int(round(done / len(criteria) * 100))


def _find_active_workflow_runner(session_id: str) -> tuple[str, Any] | None:
    """Find an active workflow runner (goal_id, runner) for a session.

    Walks ``routers.workflow._active_runners`` (in-memory). The runner's
    session_id is stored in the entry's ``session_id`` field — if it
    matches we return ``(goal_id, runner)``.
    """
    try:
        from .workflow import _active_runners, _prune_runners

        _prune_runners()
        for goal_id, entry in list(_active_runners.items()):
            if entry.get("session_id") == session_id:
                return goal_id, entry["runner"]
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] workflow runner lookup failed: %s", e)
    return None


def _shape_workflow_for_frontend(
    runner: Any,
    workflow_name: str,
) -> dict:
    """Map a workflow runner + its config to the frontend shape.

    Returns ``{name, nodes[], edges[], progress, agent_statuses}``.
    The frontend's workflow store expects ``nodes=[{id,label,status}]``
    and ``edges=[{id,source,target}]``; ``agent_statuses`` is the live
    runner dict used to derive per-node status.
    """
    config = getattr(runner, "_config", None)
    agent_status_map: dict[str, str] = {}
    progress: dict | None = None
    try:
        progress = runner.get_progress()
        agent_status_map = progress.get("agent_statuses", {}) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] get_progress failed: %s", e)

    cfg_agents = getattr(config, "agents", []) if config else []
    cfg_dag = getattr(config, "dag", {}) if config else {}

    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", None) or ""
        # Map backend status → frontend DAG node status (pending |
        # running | completed | failed). Backend uses pending|running|
        # success|skipped|error; we collapse success+skipped →
        # completed and error → failed.
        raw = agent_status_map.get(agent_id, "pending")
        node_status = _MAP_AGENT_STATUS_TO_NODE.get(raw, "pending")
        nodes.append({
            "id": agent_id,
            "label": agent_id,
            "type": "agent",
            "status": node_status,
        })

    edges = []
    for src, targets in (cfg_dag or {}).items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})

    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": progress,
        "agent_statuses": agent_status_map,
    }


_MAP_AGENT_STATUS_TO_NODE = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
}


def _shape_agents_for_frontend(
    session_id: str,
    workflow: dict | None,
) -> list[dict]:
    """Build the per-session Agent store entries.

    Each entry matches the Agent interface in stores/agents.ts minimal
    fields: id, session_id, status, name, created_at, updated_at,
    tool_calls_count, compaction_count, context_tokens,
    context_tokens_limit, iterations_detail. We populate only what we
    can derive from the workflow snapshot — the SSE stream refreshes
    the rest after the backfill (e.g. token usage, iteration detail).
    """
    if not workflow:
        return []
    status_map = workflow.get("agent_statuses", {}) or {}
    nodes = workflow.get("nodes") or []
    now = time.time()
    agents = []
    for node in nodes:
        agent_id = node["id"]
        raw_status = status_map.get(agent_id, "pending")
        # Map backend status → frontend AgentStatus
        fe_status = _MAP_AGENT_STATUS_TO_AGENT.get(raw_status, "pending")
        agents.append({
            "id": agent_id,
            "session_id": session_id,
            "status": fe_status,
            "name": agent_id,
            "description": "",
            "created_at": now,
            "updated_at": now,
            "tool_calls_count": 0,
            "compaction_count": 0,
            "context_tokens": 0,
            "context_tokens_limit": 0,
            "iterations_detail": [],
        })
    return agents


_MAP_AGENT_STATUS_TO_AGENT = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
    "aborted": "aborted",
}


@router.get("/{session_id}/state")
async def get_session_state(session_id: str, request: Request):
    """Backfill agent / DAG / goal panels after a page reload (B13).

    Aggregates three sources:
      1. GoalStore.get_current_snapshot — goal + criteria + evidence
      2. Active workflow runner (in-memory) — agent_statuses + DAG
      3. GoalWorkflowConfig — DAG structure (nodes/edges) for the
         session's active workflow

    Each subsection gracefully degrades to null / empty when the
    session has no active goal or no in-flight workflow (a finished
    workflow's runner is pruned by ``_prune_runners``; for those
    sessions we still surface the goal snapshot and the config-derived
    DAG via the goal's workflow_name, if available).

    Returns:
      {
        "goal": <frontend-shaped goal or null>,
        "workflow": <frontend-shaped workflow or null>,
        "agents": [<frontend-shaped agent>, ...]  # possibly empty
      }
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)

    # Goal snapshot
    snapshot = _build_goal_snapshot(request, session_id)
    goal = _shape_goal_for_frontend(snapshot)

    # Workflow: prefer a live runner, else attempt to derive the DAG
    # from the goal's stored workflow_name (preserved on goal record
    # by GoalWorkflowRunner.start).
    workflow: dict | None = None
    runner_match = _find_active_workflow_runner(session_id)
    if runner_match:
        _goal_id, runner = runner_match
        wf_name = getattr(getattr(runner, "_config", None), "name", "") or ""
        workflow = _shape_workflow_for_frontend(runner, wf_name)
    elif snapshot:
        # No live runner but the goal may have run a workflow — try to
        # surface the static DAG structure for the panel. GoalRecord
        # stores the workflow config name in the ``workflow_id`` column
        # (set by GoalWorkflowRunner.start — see workflow.py:378).
        goal_rec = snapshot.get("goal") or {}
        wf_name = goal_rec.get("workflow_id") or ""
        if wf_name:
            try:
                from ...core.goal.workflow_config import load_goal_workflow

                cfg = load_goal_workflow(wf_name)
                workflow = _shape_workflow_for_frontend_from_config(cfg, wf_name)
            except Exception as e:  # noqa: BLE001
                logger.debug("[state] static DAG rebuild failed: %s", e)

    agents = _shape_agents_for_frontend(session_id, workflow)

    return {
        "goal": goal,
        "workflow": workflow,
        "agents": agents,
    }


def _shape_workflow_for_frontend_from_config(cfg: Any, workflow_name: str) -> dict:
    """Build the workflow subsection from a config-only source.

    Used when there is no in-memory runner — the DAG and agent roster
    still come from the workflow's YAML. All agent statuses default to
    ``pending`` and ``progress`` is null (no live runner to ask).
    """
    cfg_agents = getattr(cfg, "agents", []) or []
    cfg_dag = getattr(cfg, "dag", {}) or {}
    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", "") or ""
        nodes.append({"id": agent_id, "label": agent_id, "type": "agent", "status": "pending"})
    edges = []
    for src, targets in cfg_dag.items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})
    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": None,
        "agent_statuses": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# FTS5 search endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _make_snippet(content: str, query: str, max_len: int = 120) -> str:
    """Extract a snippet around the first match, escaping HTML and wrapping matches in <mark>."""
    import html

    if not content:
        return ""
    if not query:
        return html.escape(content[:max_len])
    # Find first occurrence of any query token (whitespace-split)
    tokens = [t for t in query.split() if t]
    lower = content.lower()
    first_pos = -1
    for tok in tokens:
        pos = lower.find(tok.lower())
        if pos != -1 and (first_pos == -1 or pos < first_pos):
            first_pos = pos
    if first_pos == -1:
        snippet = content[:max_len]
    else:
        # Window around the match
        start = max(0, first_pos - 30)
        end = min(len(content), first_pos + max_len - 30)
        snippet = content[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"
    escaped = html.escape(snippet)
    # Highlight tokens (case-insensitive)
    for tok in tokens:
        if not tok:
            continue
        pattern = html.escape(tok)
        # Use simple find/replace with case-insensitive matching via lowered scan
        out_parts = []
        cursor = 0
        low = escaped.lower()
        while True:
            idx = low.find(pattern.lower(), cursor)
            if idx == -1:
                out_parts.append(escaped[cursor:])
                break
            out_parts.append(escaped[cursor:idx])
            out_parts.append(f"<mark>{escaped[idx:idx + len(pattern)]}</mark>")
            cursor = idx + len(pattern)
        escaped = "".join(out_parts)
    return escaped


@router.post("/search")
async def search_sessions(body: SearchRequest, request: Request):
    """Full-text search across all messages.

    Returns hits with session metadata + highlighted snippet. Joins with
    sessions table to filter by current user_id.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    # Detect if FTS5 is available
    has_fts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone() is not None
    if not has_fts:
        raise HTTPException(status_code=503, detail="Full-text search is unavailable on this build")
    if not body.query or not body.query.strip():
        return {"hits": [], "query": body.query}

    # Build FTS5 MATCH expression. For multi-word queries, AND each token
    # (so "alpha 策略" → "alpha AND 策略"). For single word, use as-is.
    tokens = [t for t in body.query.split() if t]
    # Strip FTS5 reserved chars from each token to avoid syntax errors
    safe_tokens = [t.replace('"', '').replace("'", '').replace('(', '').replace(')', '') for t in tokens]
    if not safe_tokens:
        return {"hits": [], "query": body.query}
    if len(safe_tokens) == 1:
        match_expr = safe_tokens[0]
    else:
        match_expr = " AND ".join(f'"{tok}"' for tok in safe_tokens if tok)

    rows = conn.execute(
        """
        SELECT
            m.id AS message_id,
            m.session_id,
            m.role,
            m.content,
            m.created_at,
            s.title AS session_title,
            fts.rank AS score
        FROM messages_fts fts
        JOIN messages m ON m.rowid = fts.rowid
        JOIN sessions s ON s.id = m.session_id
        WHERE messages_fts MATCH ?
          AND s.user_id = ?
          AND s.archived = 0
        ORDER BY fts.rank
        LIMIT ?
        """,
        (match_expr, user_id, body.limit),
    ).fetchall()

    hits = []
    for r in rows:
        hits.append(
            {
                "session_id": r["session_id"],
                "session_title": r["session_title"],
                "message_id": r["message_id"],
                "role": r["role"],
                "snippet": _make_snippet(r["content"], body.query),
                "score": float(r["score"]) if r["score"] is not None else 0.0,
                "created_at": r["created_at"],
            }
        )
    return {"hits": hits, "query": body.query}
