"""SessionService: unified orchestration for chat (API + TUI).

Borrowed from vibe_trading ``src/session/service.py``. Adapted to:
- Reuse strategy-research's SQLite SessionStore instead of filesystem
- Preserve auto_title_session (strategy-research advantage)
- Preserve double-ID fix (assistant message gets attempt.message_id,
  user message gets auto UUID — prevents SSE event from clobbering
  the user message after loadMessages re-keys the store by DB id)
- Accept model parameter for multi-model routing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from strategy_research.core.agent.compact import CompactConfig, compact_messages
from .events import EventBus
from .models import Attempt, AttemptStatus, Message
from .store import SessionStore

logger = logging.getLogger(__name__)

# Character budget for history (~3000 tokens) — borrowed verbatim from vibe_trading.
MAX_HISTORY_CHARS = 12000

# Hard limit for queued messages per session. Exceeding returns 429 to caller.
_QUEUE_LIMIT = 10


def _bootstrap_workspace(workspace: Path) -> None:
    """Ensure workspace has the required directory structure.

    Only creates directories — no root-level template files to avoid
    confusing the LLM about where actual strategies live.
    """
    # Create strategies directory
    strategies_dir = workspace / "strategies"
    strategies_dir.mkdir(parents=True, exist_ok=True)

    # Ensure data.duckdb exists (init_db handles table creation)
    db_path = workspace / "data.duckdb"
    if not db_path.exists():
        try:
            from ...core.db import init_db
            init_db(workspace)
        except Exception as exc:
            logger.warning("Failed to init DuckDB: %s", exc)


class SessionService:
    """Unified chat service used by both API and TUI paths.

    Attributes:
        store: SQLite-backed SessionStore.
        event_bus: SSE/EventBus for streaming events.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self._active_loops: dict[str, "asyncio.Task"] = {}
        # Per-session FIFO message queue (see docs/chat-message-queue-design.md)
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._processing_sessions: set[str] = set()
        self._paused_sessions: dict[str, asyncio.Event] = {}
        self._queue_consumers: dict[str, "asyncio.Task"] = {}

    # ── Session lifecycle ──────────────────────────────────────────────

    def create_session(self, session_id: str, title: str = "") -> dict[str, Any]:
        """Create a new session row if it doesn't exist.

        Returns the session metadata dict.
        """
        import time

        from ..routers.web_session import _get_db

        now = time.time()
        with _get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
                    "starred, tags_json, message_count, archived) "
                    "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
                    (session_id, "anonymous", title or "新会话", now, now),
                )
                conn.commit()

        self.event_bus.emit(
            session_id,
            "session.created",
            {"session_id": session_id, "title": title},
        )
        return {"id": session_id, "title": title or "新会话"}

    # ── Public entry point (shared by API and TUI) ─────────────────────

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        model: str | None = None,
        max_iterations: int = 9999999999,
        system_prompt: Optional[str] = None,
        allow_shell_tools: bool = False,
    ) -> dict[str, str]:
        """Send a user message and enqueue background AgentLoop execution.

        Messages are processed FIFO per session via ``_process_session_queue``.
        If a session already has an in-flight attempt, new messages are queued
        behind it. A hard limit of 10 queued items is enforced; exceeding it
        returns ``{"error": "queue_full", "limit": 10}``.

        Args:
            session_id: Target session.
            content: User message text.
            model: Optional LLM model override (multi-model routing).
            max_iterations: AgentLoop iterations (default 1 for plain chat).
            system_prompt: Optional custom system prompt (TUI goal mode uses
                a different prompt than API chat mode).
            allow_shell_tools: Whether the registry may include shell tools.

        Returns:
            ``{"message_id": ..., "attempt_id": ..., "queue_position": N}``
            for the user message and the spawned Attempt. On queue-full,
            returns ``{"error": "queue_full", "limit": 10, "current_size": N}``.
        """
        # ── 1. Persist user message ────────────────────────────────────
        # Capture created_at from a single authoritative clock (server
        # time.time()) for correct cross-exchange message ordering.
        import time
        _ts = time.time()
        user_msg_id = self.store.append_message(
            Message(session_id=session_id, role="user", content=content),
            created_at=_ts,
        )

        # ── 2. Auto-title on first user message ───────────────────────
        try:
            from ..routers.web_session import _get_db, auto_title_session

            new_title = auto_title_session(session_id, content)
            if new_title:
                conn = _get_db()
                row = conn.execute(
                    "SELECT message_count, starred, tags_json, archived "
                    "FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                meta_update = {
                    "session_id": session_id,
                    "title": new_title,
                    "message_count": row["message_count"] if row else 0,
                    "starred": bool(row["starred"]) if row else False,
                    "tags": json.loads(row["tags_json"]) if row and row["tags_json"] else [],
                    "archived": bool(row["archived"]) if row else False,
                }
                self.event_bus.emit(session_id, "session_meta_updated", meta_update)
        except Exception as exc:
            logger.warning("auto_title_session failed: %s", exc)

        # ── 3. Queue length hard limit ────────────────────────────────
        queue = self._session_queues.get(session_id)
        current_size = queue.qsize() if queue is not None else 0
        if current_size >= _QUEUE_LIMIT:
            return {
                "error": "queue_full",
                "limit": _QUEUE_LIMIT,
                "current_size": current_size,
                "message_id": user_msg_id,
            }

        # ── 4. Create Attempt ─────────────────────────────────────────
        attempt = Attempt(
            session_id=session_id,
            prompt=content,
            message_id=str(uuid.uuid4()),
            status=AttemptStatus.PENDING,
            created_at=_utc_now_iso(),
        )
        self.store.create_attempt(attempt)
        self.event_bus.emit(
            session_id,
            "attempt.created",
            {"attempt_id": attempt.attempt_id, "prompt": content},
        )

        # ── 5. Compute queue position + status ────────────────────────
        queue_position = current_size + 1  # 1-based
        is_first = (
            session_id not in self._processing_sessions
            and queue_position == 1
        )
        status = "processing" if is_first else "queued"

        # ── 6. Emit message_received with queue metadata ──────────────
        self.event_bus.emit(
            session_id,
            "message_received",
            {
                "message_id": user_msg_id,
                "user_message_id": user_msg_id,
                "assistant_message_id": attempt.message_id,
                "role": "user",
                "content": content,
                "attempt_id": attempt.attempt_id,
                "status": status,
                "queue_position": queue_position,
                "queue_length": queue_position,  # grows as items are added; later items will see actual length
                "created_at": _ts,
            },
        )

        # ── 7. Enqueue + start consumer if not running ────────────────
        if queue is None:
            queue = asyncio.Queue()
            self._session_queues[session_id] = queue
        await queue.put(
            {
                "attempt_id": attempt.attempt_id,
                "model": model,
                "max_iterations": max_iterations,
                "system_prompt": system_prompt,
                "allow_shell_tools": allow_shell_tools,
            }
        )
        # Recompute queue_length now that we've enqueued (best-effort snapshot
        # for frontend display; full count will be consistent within tolerance).
        self.event_bus.emit(
            session_id,
            "queue_state",
            {
                "session_id": session_id,
                "queue_length": queue.qsize(),
            },
        )

        if session_id not in self._processing_sessions:
            self._processing_sessions.add(session_id)
            consumer = asyncio.create_task(self._process_session_queue(session_id))
            self._queue_consumers[session_id] = consumer
            consumer.add_done_callback(
                lambda t: self._queue_consumers.pop(session_id, None)
            )

        return {
            "message_id": user_msg_id,
            "user_message_id": user_msg_id,
            "assistant_message_id": attempt.message_id,
            "attempt_id": attempt.attempt_id,
            "queue_position": queue_position,
            "status": status,
        }

    def cancel(self, attempt_id: str) -> bool:
        """Cancel an in-flight Attempt by its id.

        Looks up the per-attempt task created inside ``_process_session_queue``
        and cancels it. The consumer loop catches ``CancelledError`` and pauses
        the queue until ``resume_queue()`` is called.
        """
        task = self._active_loops.get(attempt_id)
        if task is None:
            return False
        task.cancel()
        return True

    def cancel_session(self, session_id: str) -> bool:
        """Cancel the in-flight attempt for a session (if any).

        Used when the caller knows the session but not the attempt id
        (e.g. the frontend's Cancel button). Returns True if cancelled.
        """
        consumer = self._queue_consumers.get(session_id)
        if consumer is None:
            return False
        consumer.cancel()
        return True

    def resume_queue(self, session_id: str) -> bool:
        """Resume a paused queue after an explicit cancel.

        Returns True if a paused session was found and resumed.
        """
        event = self._paused_sessions.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    async def _process_session_queue(self, session_id: str) -> None:
        """Consumer coroutine for a session's message queue.

        Drains ``self._session_queues[session_id]`` one item at a time,
        running each as ``_run_attempt``. After an explicit cancel
        (``CancelledError`` raised by ``cancel(attempt_id)``), pauses
        the queue and emits ``queue_paused`` until ``resume_queue``
        sets the resume event.
        """
        queue = self._session_queues[session_id]
        try:
            while True:
                item = await queue.get()

                # If a previous attempt was explicitly cancelled, the queue is
                # paused. Wait for resume before processing the next item.
                pause_event = self._paused_sessions.get(session_id)
                if pause_event is not None:
                    await pause_event.wait()
                    self._paused_sessions.pop(session_id, None)

                # Resolve the attempt row from DB
                attempt = self.store.get_attempt(session_id, item["attempt_id"])
                if attempt is None:
                    logger.warning(
                        "Attempt %s vanished from DB before queue consumer "
                        "could run it",
                        item["attempt_id"],
                    )
                    queue.task_done()
                    continue

                # Run the attempt as a tracked task so cancel(attempt_id) works
                attempt_task = asyncio.create_task(
                    self._run_attempt(
                        session_id=session_id,
                        attempt=attempt,
                        model=item.get("model"),
                        max_iterations=item.get("max_iterations", 1),
                        system_prompt=item.get("system_prompt"),
                        allow_shell_tools=item.get("allow_shell_tools", False),
                    )
                )
                self._active_loops[attempt.attempt_id] = attempt_task

                try:
                    await attempt_task
                except asyncio.CancelledError:
                    # User explicit cancel — pause the queue and emit SSE so
                    # the frontend can render the "队列已暂停" banner.
                    self.event_bus.emit(
                        session_id,
                        "queue_paused",
                        {
                            "session_id": session_id,
                            "next_attempt_id": attempt.attempt_id,
                        },
                    )
                    pause_evt = asyncio.Event()
                    self._paused_sessions[session_id] = pause_evt
                    # Wait for resume before continuing the loop
                    await pause_evt.wait()
                    self._paused_sessions.pop(session_id, None)
                    queue.task_done()
                    continue
                except Exception as exc:
                    logger.exception(
                        "Queue consumer: attempt %s failed: %s",
                        attempt.attempt_id,
                        exc,
                    )
                finally:
                    self._active_loops.pop(attempt.attempt_id, None)

                queue.task_done()

                if queue.empty():
                    break
        finally:
            self._processing_sessions.discard(session_id)
            # Keep the queue object around briefly so a follow-up send_message
            # call finds it and reuses it; the next consumer task will
            # naturally drain it. But if truly empty, drop it.
            q = self._session_queues.get(session_id)
            if q is not None and q.empty():
                self._session_queues.pop(session_id, None)

    # ── Attempt execution ──────────────────────────────────────────────

    async def _run_attempt(
        self,
        *,
        session_id: str,
        attempt: Attempt,
        model: Optional[str],
        max_iterations: int,
        system_prompt: Optional[str],
        allow_shell_tools: bool,
    ) -> None:
        """Execute an Attempt: load history → run AgentLoop → persist result."""
        attempt.mark_running()
        self.store.update_attempt(attempt)
        # attempt.started carries message_id so the frontend can switch its
        # streamingMessageId from queued placeholder to actual stream.
        self.event_bus.emit(
            session_id,
            "attempt.started",
            {
                "attempt_id": attempt.attempt_id,
                "message_id": attempt.message_id,
            },
        )

        accumulated_parts: list[dict[str, Any]] = []

        try:
            # 1. Load full message history from DB and convert to OpenAI-format
            messages = self.store.get_messages(session_id, limit=100)
            history = self._convert_messages_to_history(messages)

            # 2. Run AgentLoop with history context
            result_dict = await self._run_with_agent(
                attempt=attempt,
                history=history,
                model=model,
                max_iterations=max_iterations,
                system_prompt=system_prompt,
                allow_shell_tools=allow_shell_tools,
                accumulated_parts=accumulated_parts,
            )

            # 3. Update Attempt and persist assistant message
            answer = result_dict.get("content") or ""
            status = result_dict.get("status", "empty")
            if status == "success":
                attempt.mark_completed(summary=answer)
            elif status == "failed":
                attempt.mark_failed(error=result_dict.get("reason", "unknown"))
            else:
                # "empty" — agent ran but produced no output (e.g. LLM error
                # was swallowed by AgentLoop and emitted via SSE). Don't mark
                # as failed; the error event is already in the SSE stream.
                attempt.mark_completed(summary="")
            attempt.run_dir = result_dict.get("run_dir")
            attempt.metrics = result_dict.get("metrics")
            attempt.message_id = result_dict.get("message_id") or attempt.message_id
            self.store.update_attempt(attempt)

            # Persist assistant message with the Attempt's message_id
            # (this is the SAME id SSE events carry, so they can find it).
            assistant_content = answer or "".join(
                p.get("text", "") for p in accumulated_parts if p.get("type") == "text"
            )
            self.store.append_message(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    linked_attempt_id=attempt.attempt_id,
                    metadata={
                        "run_id": Path(attempt.run_dir).name if attempt.run_dir else None,
                        "status": attempt.status.value,
                        "metrics": attempt.metrics,
                    },
                ),
                message_id=attempt.message_id,
                parts=accumulated_parts or None,
            )

            # 4. Emit attempt.completed / attempt.failed
            self.event_bus.emit(
                session_id,
                "attempt.completed" if attempt.status == AttemptStatus.COMPLETED else "attempt.failed",
                {
                    "attempt_id": attempt.attempt_id,
                    "status": attempt.status.value,
                    "summary": attempt.summary,
                    "error": attempt.error,
                    "run_dir": attempt.run_dir,
                    "message_id": attempt.message_id,
                },
            )

            # 5. Signal agent_done so the frontend clears streaming state
            self.event_bus.emit(
                session_id,
                "agent_done",
                {"message_id": attempt.message_id, "status": attempt.status.value},
            )
        except asyncio.CancelledError:
            attempt.mark_failed(error="cancelled")
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session_id,
                "attempt.failed",
                {"attempt_id": attempt.attempt_id, "status": "cancelled"},
            )
            self.event_bus.emit(
                session_id,
                "agent_done",
                {"message_id": attempt.message_id, "status": "cancelled"},
            )
            raise
        except Exception as exc:
            logger.exception("AgentLoop failed for attempt %s", attempt.attempt_id)
            attempt.mark_failed(error=str(exc))
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session_id,
                "attempt.failed",
                {"attempt_id": attempt.attempt_id, "error": str(exc)},
            )
            self.event_bus.emit(
                session_id,
                "agent_done",
                {"message_id": attempt.message_id, "status": "error", "error": str(exc)},
            )

    async def _run_with_agent(
        self,
        *,
        attempt: Attempt,
        history: list[dict[str, Any]],
        model: Optional[str],
        max_iterations: int,
        system_prompt: Optional[str],
        allow_shell_tools: bool,
        accumulated_parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build AgentLoop and run it. Returns ``{content, status, ...}``."""
        from strategy_research.core.agent.builtin_tools import build_default_registry
        from strategy_research.core.agent.loop import AgentLoop

        # Build LLM config — reuse the existing _build_llm_config() from chat.py
        # which correctly loads ~/.quantnodes/.env and resolves api_key.
        from ..routers.chat import _build_llm_config

        cfg = _build_llm_config()
        if cfg and model:
            cfg.model = model

        # Build tool registry
        try:
            registry = build_default_registry()
        except Exception:
            registry = None

        # Default system prompt: chat mode
        if system_prompt is None:
            try:
                from ..routers.chat import _get_system_prompt

                system_prompt = _get_system_prompt()
            except Exception:
                system_prompt = "你是 QuantNodes-Research 的量化金融助手。"

        # Event callback: forward AgentLoop events → EventBus.
        # Each event carries message_id so SSE clients can correlate.
        # Also accumulates llm_usage tokens into attempt-local metrics so
        # the frontend can show context usage progress.
        usage_lock = threading.Lock()
        usage_state: dict[str, int] = {"input": 0, "output": 0}

        def event_callback(event_type: str, data: dict[str, Any]) -> None:
            # Accumulate parts for persistence
            _accumulate_part(accumulated_parts, event_type, data)

            # Persist tool result messages immediately as role=tool rows
            # so the next AgentLoop invocation can rebuild full history.
            if event_type == "tool_result":
                tc_id = data.get("id") or data.get("call_id")
                result_text = data.get("result") or data.get("preview") or ""
                if tc_id and result_text:
                    try:
                        self.store.append_message(
                            Message(
                                session_id=attempt.session_id,
                                role="tool",
                                content=result_text,
                                tool_call_id=tc_id,
                                metadata={"attempt_id": attempt.attempt_id},
                            ),
                        )
                    except Exception as exc:
                        logger.warning("persist tool result failed: %s", exc)

            # Track token usage so the frontend can show a context
            # usage bar. llm_usage is emitted by AgentLoop after each
            # LLM call (see core/agent/loop.py).
            if event_type == "llm_usage" and isinstance(data, dict):
                with usage_lock:
                    # OpenAI-compatible providers use input_tokens /
                    # output_tokens; some send prompt_tokens /
                    # completion_tokens. Accept both.
                    inc_in = int(
                        data.get("input_tokens")
                        or data.get("prompt_tokens")
                        or 0
                    )
                    inc_out = int(
                        data.get("output_tokens")
                        or data.get("completion_tokens")
                        or 0
                    )
                    usage_state["input"] += inc_in
                    usage_state["output"] += inc_out
                total = usage_state["input"] + usage_state["output"]
                # Emit a session_total_tokens event so the frontend
                # has an authoritative figure (not the per-call delta).
                self.event_bus.emit(
                    attempt.session_id,
                    "session_total_tokens",
                    {
                        "input_tokens": usage_state["input"],
                        "output_tokens": usage_state["output"],
                        "total_tokens": total,
                        "message_id": attempt.message_id,
                        "attempt_id": attempt.attempt_id,
                    },
                )

            # Add attempt/message context
            data = dict(data)
            data.setdefault("attempt_id", attempt.attempt_id)
            data.setdefault("message_id", attempt.message_id)
            self.event_bus.emit(attempt.session_id, event_type, data)

        # Build AgentLoop
        workspace_path = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.cwd())))

        # Bootstrap workspace if incomplete
        _bootstrap_workspace(workspace_path)

        agent = AgentLoop(
            config=cfg,
            registry=registry,
            workspace=workspace_path,
            on_event=event_callback,
            stream_mode=True,
            max_iterations=max_iterations,
            session_id=attempt.session_id,
            system_prompt=system_prompt,
            allowed_tools=None,
            compact_config=cfg.compact_config,
        )

        # Run synchronously inside the asyncio loop (AgentLoop.arun is async).
        try:
            loop_result = await agent.arun(attempt.prompt, history=history)
        except Exception as exc:
            logger.exception("AgentLoop.arun failed")
            return {"status": "failed", "reason": str(exc), "content": ""}

        return {
            "status": "success" if loop_result.answer else "empty",
            "content": loop_result.answer or "",
            "run_dir": None,
            "iterations": loop_result.iterations,
            "tool_calls_made": loop_result.tool_calls_made,
            "finished_reason": loop_result.finished_reason,
            "metrics": {
                "input_tokens": usage_state["input"],
                "output_tokens": usage_state["output"],
                "total_tokens": usage_state["input"] + usage_state["output"],
            },
        }

    # ── Compact ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_summary(messages: list[dict[str, Any]]) -> str:
        """Extract [context summary] content from compressed messages."""
        parts = []
        for m in messages:
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if content.startswith("[context summary]"):
                    parts.append(content[len("[context summary]"):].strip())
        return "\n\n".join(parts)

    @staticmethod
    def _build_fallback_summary(
        compressed: list[dict[str, Any]],
        original: list[dict[str, Any]],
    ) -> str:
        """Build a basic structured summary when LLM summarization didn't run."""
        # Collect assistant text snippets from original (last 5 turns)
        assistant_snippets: list[str] = []
        user_msgs: list[str] = []
        for m in original:
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            if not content or content.startswith("[context summary]"):
                continue
            if role == "assistant" and len(content) > 20:
                # Keep first 200 chars of each assistant message
                assistant_snippets.append(content[:200])
            elif role == "user":
                user_msgs.append(content[:100])

        # Last user message = likely current objective
        objective = user_msgs[-1] if user_msgs else "(none)"

        # Build structured summary
        lines = [
            "## Objective",
            f"- {objective}",
            "",
            "## Important Details",
        ]
        if len(compressed) < len(original):
            removed = len(original) - len(compressed)
            lines.append(f"- {removed} messages removed during compaction")
        else:
            lines.append("- (none)")
        lines += [
            "",
            "## Work State",
            "### Completed",
        ]
        # Last 2 assistant snippets as completed work
        for snippet in assistant_snippets[-2:]:
            lines.append(f"- {snippet[:150]}")
        if not assistant_snippets:
            lines.append("- (none)")
        lines += [
            "",
            "### Active",
            "- (none)",
            "",
            "### Blocked",
            "- (none)",
            "",
            "## Next Move",
            "1. Continue from where the conversation left off",
            "",
            "## Relevant Files",
            "- (none)",
        ]
        return "\n".join(lines)

    async def compact_history(
        self,
        session_id: str,
        config: CompactConfig | None = None,
    ) -> dict[str, Any]:
        """Compress session history in-place using compact_messages.

        Writes compressed results back to the database.
        Returns dict with keys: layers, before_tokens, after_tokens, summary.
        """
        from ..routers.chat import _build_llm_config
        from ..routers.web_session import delete_messages

        messages = self.store.get_messages(session_id, limit=10000)
        history = self._convert_messages_to_history(messages)

        if not history:
            return {"layers": [], "before_tokens": 0, "after_tokens": 0, "summary": ""}

        cfg = _build_llm_config()
        llm_client = None
        model_context_tokens = None
        if cfg:
            model_context_tokens = cfg.model_context_tokens
            try:
                from strategy_research.core.llm import OpenAICompatClient
                llm_client = OpenAICompatClient(cfg)
            except Exception:
                pass

        compressed, layers = compact_messages(
            history,
            config=config,
            threshold_tokens=0,  # force all layers for manual /compact
            model_context_tokens=model_context_tokens,
            llm_client=llm_client,
        )

        before_tokens = sum(len(m.get("content", "")) for m in history)
        after_tokens = sum(len(m.get("content", "")) for m in compressed)

        # Build summary: prefer LLM-generated [context summary], fallback to extraction
        summary = self._extract_summary(compressed)
        if not summary and layers:
            summary = self._build_fallback_summary(compressed, history)

        # Persist compressed history back to DB (replace old messages)
        if layers:
            try:
                # Collect IDs of all non-last messages (the ones that were compressed)
                old_ids = [m.message_id for m in messages[:-1]]
                if old_ids:
                    delete_messages(session_id, old_ids)

                # Insert compressed messages in order, assigning fresh IDs
                import uuid
                new_ids: list[str] = []
                for i, m in enumerate(compressed):
                    mid = f"cmp_{uuid.uuid4().hex[:10]}"
                    new_ids.append(mid)
                    role = m.get("role", "assistant")
                    content = m.get("content", "") or ""
                    tool_call_id = m.get("tool_call_id")

                    # Build parts for assistant messages with tool_calls
                    parts = None
                    if role == "assistant" and m.get("tool_calls"):
                        parts = []
                        for tc in m["tool_calls"]:
                            parts.append({
                                "type": "tool_call",
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": tc.get("function", {}).get("arguments", "{}"),
                                "status": "done",
                                "result": "",
                            })

                    self.store.append_message(
                        Message(
                            session_id=session_id,
                            role=role,
                            content=content,
                            tool_call_id=tool_call_id,
                            metadata={"compacted": True, "order": i},
                        ),
                        message_id=mid,
                        parts=parts,
                    )
            except Exception:
                logger.exception("failed to persist compacted history")

        return {
            "layers": layers,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "summary": summary,
            "compressed": compressed,
        }

    # ── History conversion ────────────────────────────────────────────

    @staticmethod
    def _convert_messages_to_history(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Session messages to OpenAI-format history.

        Handles two storage formats:
        1. New: role=tool messages with tool_call_id (1:1 with OpenAI format)
        2. Legacy: tool_calls embedded in assistant message parts (reconstructed from parts)

        Excludes the current turn (last message). Trims by character budget
        from the newest items so the LLM still sees the most recent context.
        """
        history: list[dict[str, Any]] = []
        for msg in messages[:-1]:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            parts = msg.metadata.get("_parts", []) if hasattr(msg, "metadata") else []

            if role == "tool":
                tc_id = msg.tool_call_id if hasattr(msg, "tool_call_id") else None
                if not tc_id:
                    continue
                history.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": content or "",
                })
                continue

            if role not in ("user", "assistant"):
                continue

            entry: dict[str, Any] = {"role": role, "content": content or ""}

            if role == "assistant" and parts:
                tool_calls = []
                for p in parts:
                    if p.get("type") != "tool_call":
                        continue
                    tc_id = p.get("id") or p.get("call_id")
                    if not tc_id:
                        continue
                    args_str = p.get("arguments") or "{}"
                    if not isinstance(args_str, str):
                        args_str = json.dumps(args_str, ensure_ascii=False)
                    tool_calls.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": p.get("name", ""),
                            "arguments": args_str,
                        },
                    })
                if tool_calls:
                    entry["tool_calls"] = tool_calls

            if not content and not entry.get("tool_calls"):
                continue

            history.append(entry)

        # Trim by character budget from newest → oldest, but never break
        # an assistant message with tool_calls away from its tool results.
        total_chars = 0
        trimmed: list[dict[str, Any]] = []
        for msg in reversed(history):
            msg_len = len(msg.get("content", ""))
            for tc in msg.get("tool_calls") or []:
                msg_len += len(tc.get("function", {}).get("arguments", ""))
            if total_chars + msg_len > MAX_HISTORY_CHARS:
                break
            trimmed.append(msg)
            total_chars += msg_len
        return list(reversed(trimmed))


# ── Helpers ────────────────────────────────────────────────────────────


def _accumulate_part(
    parts: list[dict[str, Any]],
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Accumulate SSE event payloads into a structured parts list.

    Mirrors the logic in chat.py:_run_agent_loop_background.
    """
    if event_type == "text_delta":
        text = data.get("text", "")
        if parts and parts[-1].get("type") == "text":
            parts[-1]["text"] += text
        else:
            parts.append({"type": "text", "text": text})
    elif event_type == "tool_call":
        raw_args = data.get("arguments")
        args_str = (
            raw_args
            if isinstance(raw_args, str)
            else json.dumps(raw_args, ensure_ascii=False) if raw_args is not None else None
        )
        parts.append(
            {
                "type": "tool_call",
                "id": data.get("id") or data.get("call_id"),
                "name": data.get("name") or data.get("tool"),
                "arguments": args_str,
            }
        )
    elif event_type == "tool_result":
        tool_call_id = data.get("id") or data.get("call_id")
        for p in reversed(parts):
            if p.get("type") == "tool_call" and p.get("id") == tool_call_id:
                p["result"] = data.get("result") or data.get("preview")
                p["status"] = data.get("status", "done")
                break
    elif event_type == "thinking_delta":
        delta = data.get("delta", "")
        if parts and parts[-1].get("type") == "thinking":
            parts[-1]["text"] += delta
        else:
            parts.append({"type": "thinking", "text": delta})
    elif event_type == "thinking_done":
        if parts and parts[-1].get("type") == "thinking":
            parts[-1]["status"] = "done"


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


__all__ = ["SessionService", "MAX_HISTORY_CHARS"]
