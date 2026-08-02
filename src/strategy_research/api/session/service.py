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
from strategy_research.core.llm import LLMConfig

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
        logger.info("[MSG] send_message session=%s content_len=%d", session_id, len(content))

        # ── 1. Generate user message ID + timestamp ────────────────────
        # B4: user message is persisted via EventBusV2 → projector.flush(),
        # not via direct store.append_message(). The message_received event
        # carries the same data that the old direct write used.
        import time
        _ts = time.time()
        user_msg_id = str(uuid.uuid4())
        logger.info("[MSG] user_msg_id=%s", user_msg_id)

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

    async def wait_for_attempt(
        self,
        session_id: str,
        attempt_id: str,
        timeout: float = 600.0,
    ) -> Optional[dict[str, str]]:
        """Wait for a background attempt to finish; return its outcome.

        Polls the attempts table (the source of truth for status).
        Used by the synchronous /api/chat/send path, which must block
        until the per-session FIFO consumer has run the attempt.

        Returns ``{"status", "summary", "error"}`` on completion, or
        None when the timeout elapses while the attempt is still
        pending/running (e.g. a long queue ahead of it).
        """
        terminal = ("completed", "failed", "cancelled")
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            attempt = self.store.get_attempt(session_id, attempt_id)
            if attempt is not None and attempt.status in terminal:
                return {
                    "status": attempt.status,
                    "summary": attempt.summary or "",
                    "error": attempt.error or "",
                }
            if asyncio.get_event_loop().time() > deadline:
                return None
            await asyncio.sleep(0.25)

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
        logger.info("[EXEC] start attempt=%s session=%s", attempt.attempt_id, session_id)
        attempt.mark_running()

        # Build LLM config early — needed for compaction filter setting
        from ..routers.chat import _build_llm_config
        cfg = _build_llm_config()
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
            logger.info("[EXEC] loaded %d messages from DB", len(messages))

            # Get keep_all_compactions setting (default False if cfg unavailable)
            keep_all = bool(
                cfg and cfg.compact_config
                and cfg.compact_config.keep_all_compactions_in_history
            )
            history = self._convert_messages_to_history(
                messages, keep_all_compactions=keep_all
            )
            logger.info("[EXEC] converted to %d history entries", len(history))

            # Log compaction messages in history
            compaction_count = sum(1 for h in history
                                  if h.get("role") == "user"
                                  and "<conversation-checkpoint>" in h.get("content", ""))
            logger.info("[EXEC] compaction_messages_in_history=%d", compaction_count)

            # 2. Run AgentLoop with history context
            logger.info("[EXEC] running agent_loop model=%s max_iter=%d", model, max_iterations)
            result_dict = await self._run_with_agent(
                attempt=attempt,
                history=history,
                model=model,
                max_iterations=max_iterations,
                system_prompt=system_prompt,
                allow_shell_tools=allow_shell_tools,
                cfg=cfg,  # Pass cfg from _run_attempt to avoid NameError
                accumulated_parts=accumulated_parts,
            )
            logger.info("[EXEC] agent_result status=%s content_len=%d",
                       result_dict.get("status"), len(result_dict.get("content", "")))

            # 3. Update Attempt and persist assistant message
            answer = result_dict.get("content") or ""
            status = result_dict.get("status", "empty")
            finished_reason = result_dict.get("finished_reason", "")
            agent_error = result_dict.get("error")
            is_error = finished_reason == "error" or status == "failed"

            if status == "success":
                attempt.mark_completed(summary=answer)
            elif is_error:
                error_msg = agent_error or result_dict.get("reason", "unknown error")
                attempt.mark_failed(error=error_msg)
            else:
                # "empty" — agent ran but produced no output
                attempt.mark_completed(summary="")
            attempt.run_dir = result_dict.get("run_dir")
            attempt.metrics = result_dict.get("metrics")
            attempt.message_id = result_dict.get("message_id") or attempt.message_id
            self.store.update_attempt(attempt)

            # Persist message with the Attempt's message_id
            # (this is the SAME id SSE events carry, so they can find it).
            assistant_content = answer or "".join(
                p.get("text", "") for p in accumulated_parts if p.get("type") == "text"
            )

            if is_error:
                # Error message: friendly text + detail in metadata
                friendly = _friendly_error_text(agent_error or result_dict.get("reason", ""))
                detail = agent_error or result_dict.get("reason", "unknown error")
                # B4: persisted via EventBusV2 → projector.flush()
                self.event_bus.emit(
                    session_id,
                    "assistant_message",
                    {
                        "message_id": attempt.message_id,
                        "content": friendly,
                        "message_type": "error",
                        "metadata": {
                            "status": "error",
                            "details": detail,
                        },
                    },
                )
            else:
                # B4: success path — emit assistant_message with final content
                # so the projector can finalize the message. The parts
                # (text, tool_call, thinking) were already emitted via
                # event_callback during streaming.
                self.event_bus.emit(
                    session_id,
                    "assistant_message",
                    {
                        "message_id": attempt.message_id,
                        "content": assistant_content,
                        "message_type": "assistant",
                        "metadata": {
                            "run_id": str(Path(attempt.run_dir).name) if attempt.run_dir else None,
                            "status": attempt.status.value,
                            "metrics": attempt.metrics,
                        },
                    },
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
        cfg: LLMConfig,
    ) -> dict[str, Any]:
        """Build AgentLoop and run it. Returns ``{content, status, ...}``.

        Args:
            cfg: Pre-built LLM config from caller (_run_attempt). Required
                to avoid the cfg-undefined NameError regression that
                happened when Phase 1 compaction filter changes were made.
        """
        # Test mode: when STRATEGY_RESEARCH_TEST_CHAT=1, emit a scripted
        # SSE stream and return immediately — no LLM needed. Restores the
        # pre-refactor behavior (chat.py:_run_agent_loop_background) that
        # send_async used to short-circuit to in test mode; the unified
        # SessionService path lost that hook when send_async was migrated.
        if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
            return await self._run_test_script(
                attempt=attempt,
                accumulated_parts=accumulated_parts,
            )

        from strategy_research.core.agent.builtin_tools import build_default_registry
        from strategy_research.core.agent.loop import AgentLoop

        # cfg is passed in by caller; just apply model override
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

            # B4: tool result persistence is handled by EventBusV2 →
            # projector.flush(). The tool_result event lands in event_log,
            # the projector merges it into the assistant message's tool_call
            # part, and flush writes the updated message to messages table.
            # No direct DB write needed here.

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
            event_bus=self.event_bus,
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
            "error": loop_result.error,
            "metrics": {
                "input_tokens": usage_state["input"],
                "output_tokens": usage_state["output"],
                "total_tokens": usage_state["input"] + usage_state["output"],
            },
        }

    async def _run_test_script(
        self,
        *,
        attempt: Attempt,
        accumulated_parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Emit a scripted SSE stream for E2E tests (no LLM needed).

        Mirrors the real AgentLoop event sequence (iter_start → text_delta
        → iter_end) so SSE tests are deterministic and offline. The caller
        (_run_attempt) persists the assistant message and emits
        attempt.completed / agent_done afterwards, matching production.
        """
        session_id = attempt.session_id
        message_id = attempt.message_id
        attempt_id = attempt.attempt_id
        text_id = f"txt_{uuid.uuid4().hex[:12]}"

        reply_parts = [
            f"这是对「{attempt.prompt[:40]}」的脚本化回复。",
            " 主要演示 SSE 流式事件分发链路。",
            "\n\n**测试要点**：",
            "\n- 事件 message_id 关联",
            "\n- streamingText 累积",
            "\n- agent_done 清空 streamingMessageId",
        ]
        full_text = "".join(reply_parts)

        def emit(event_type: str, data: dict[str, Any]) -> None:
            data = dict(data)
            data.setdefault("message_id", message_id)
            data.setdefault("attempt_id", attempt_id)
            self.event_bus.emit(session_id, event_type, data)

        emit("iter_start", {"iteration": 1, "max_iterations": 9999999999})
        emit("text.started", {"text_id": text_id})

        emit("thinking_start", {})
        think_chunks = ["分析用户问题", " → 检索相关策略", " → 准备回复"]
        for chunk in think_chunks:
            await asyncio.sleep(0.03)
            emit("thinking_delta", {"delta": chunk})
        emit("thinking_done", {})

        for part in reply_parts:
            await asyncio.sleep(0.03)
            emit("text_delta", {"text_id": text_id, "text": part})
        emit("thinking_end", {})
        emit("text.ended", {"text_id": text_id, "text": full_text})
        emit("iter_end", {"iteration": 1, "finish_reason": "stop", "tool_calls_made": 0})

        accumulated_parts.append({"type": "text", "text": full_text})

        return {
            "status": "success",
            "content": full_text,
            "run_dir": None,
            "iterations": 1,
            "tool_calls_made": 0,
            "finished_reason": "stop",
            "error": None,
            "metrics": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    # ── Compact ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_summary(messages: list[dict[str, Any]]) -> str:
        """Extract summary content from compressed messages.

        Supports two storage formats:
        1. New: message_type='compaction' with parts_json containing summary
        2. Legacy: content field starts with [context summary] prefix

        The legacy format check is for backward compatibility with old data.
        """
        parts = []
        for m in messages:
            msg_type = m.get("message_type") or m.get("role")
            content = m.get("content", "") or ""
            # New format: message_type='compaction' (parts_json has the summary)
            if msg_type == "compaction":
                try:
                    parts_data = m.get("parts_json") or "[]"
                    if isinstance(parts_data, str):
                        import json as _json
                        parts_data = _json.loads(parts_data)
                    comp_part = next(
                        (p for p in parts_data
                         if isinstance(p, dict) and p.get("type") == "compaction"),
                        None,
                    )
                    if comp_part:
                        parts.append(comp_part.get("summary", content))
                        continue
                except Exception:
                    pass
                parts.append(content)
            # Legacy format: content starts with [context summary]
            elif content.startswith("[context summary]"):
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
            # Skip compaction messages (new format: message_type='compaction')
            if m.get("message_type") == "compaction":
                continue
            # Skip legacy compaction messages (content starts with [context summary])
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

        messages = self.store.get_messages(session_id, limit=10000)
        # keep_all_compactions from the caller-supplied CompactConfig
        keep_all = bool(
            config
            and config.keep_all_compactions_in_history
        )
        history = self._convert_messages_to_history(
            messages, keep_all_compactions=keep_all
        )

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

        # Persist compressed history via event-sourcing (B4/B5)
        # Emit compact.ended event with the compressed message set.
        # The projector handles:
        # 1. Replacing old messages with compressed versions
        # 2. Adding a compaction marker message
        # 3. Preserving the last (current turn) message
        # EventBusV2.flush_to_messages=True ensures messages table is updated.
        if layers:
            try:
                self.event_bus.emit(
                    session_id,
                    "compact.ended",
                    {
                        "summary": summary or f"Compressed {before_tokens} → {after_tokens} tokens",
                        "before_tokens": before_tokens,
                        "after_tokens": after_tokens,
                        "layers": layers,
                        "messages": compressed,
                    },
                )
            except Exception:
                logger.exception("failed to emit compact event")

        return {
            "layers": layers,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "summary": summary,
            "compressed": compressed,
        }

    # ── History conversion ────────────────────────────────────────────

    @staticmethod
    def _convert_messages_to_history(
        messages: list[Message],
        *,
        keep_all_compactions: bool = False,
    ) -> list[dict[str, Any]]:
        """Convert Session messages to OpenAI-format history.

        Handles three storage formats:
        1. New: role=tool messages with tool_call_id (1:1 with OpenAI format)
        2. Legacy: tool_calls embedded in assistant message parts (reconstructed from parts)
        3. Compaction: message_type='compaction' → user role with checkpoint wrap

        **Assistant-Tool ordering (opencode-aligned, Level 0)**:
        For each assistant message with tool_calls, all corresponding tool
        result messages are emitted IMMEDIATELY AFTER the assistant (not
        in raw `created_at` order). This is required by the OpenAI tool
        protocol: `tool` messages must follow the `assistant(tool_calls)`
        that generated them. Violating this order causes provider errors
        like MiniMax 400 "chat content is empty" (2013).

        See opencode `to-llm-message.ts:assistant()` for the reference
        implementation: tools are physically part of the assistant message
        there, making the correct order a structural invariant. We achieve
        the same effect by reordering at conversion time.

        Trim semantics: character budget is enforced from newest → oldest,
        with assistant-tool groups kept intact (either the whole group
        fits or the whole group is dropped).

        Excludes the current turn (last message).

        Args:
            keep_all_compactions: When True, include all compaction messages
                in LLM history (legacy behavior). When False (default), only
                the MOST RECENT compaction is included; older compactions
                are hidden from LLM but kept in DB for audit/UI display.
                See docs/compaction-history-filter.md.
        """
        from strategy_research.core.agent.compact import _compaction_metrics
        from strategy_research.core.agent.compaction_message import CompactionMessage

        logger.debug("[HIST] converting %d messages", len(messages))
        _compaction_metrics["filter_calls"] += 1

        # ── First pass: locate all compaction indices ──
        compaction_indices: list[int] = []
        for i, msg in enumerate(messages[:-1]):
            mt = msg.message_type if hasattr(msg, "message_type") else "assistant"
            if mt == "compaction":
                compaction_indices.append(i)

        # ── Decide which compactions to keep in LLM context ──
        keep_compaction_indices: set[int]
        if keep_all_compactions or not compaction_indices:
            keep_compaction_indices = set(compaction_indices)
        else:
            # opencode-aligned: keep only the most recent compaction
            keep_compaction_indices = {compaction_indices[-1]}
            hidden = len(compaction_indices) - 1
            _compaction_metrics["total_hidden"] += hidden
            _compaction_metrics["total_kept"] += 1
            if hidden > 0:
                logger.debug(
                    "[HIST] hiding %d older compactions, keeping 1 most recent",
                    hidden,
                )

        # ── Pre-build indexes for assistant-tool reordering ──
        # tool_to_assistant_idx: tool_call_id -> assistant message index
        # tc_to_tool_idx: tool_call_id -> tool message index
        # These let us enforce the OpenAI protocol invariant:
        #   assistant(tool_calls) MUST be followed by its tool result(s)
        # opencode's to-llm-message.ts:assistant() guarantees this by
        # physical structure (tool is a part of the assistant message).
        # We achieve the same effect by reordering at conversion time.
        tool_to_assistant_idx: dict[str, int] = {}
        tc_to_tool_idx: dict[str, int] = {}
        for i, msg in enumerate(messages[:-1]):
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            if role == "assistant":
                parts = msg.metadata.get("_parts", []) if hasattr(msg, "metadata") else []
                for p in parts:
                    if isinstance(p, dict) and p.get("type") == "tool_call":
                        tc_id = p.get("id") or p.get("call_id")
                        if tc_id:
                            tool_to_assistant_idx.setdefault(tc_id, i)
            elif role == "tool":
                tc_id = msg.tool_call_id if hasattr(msg, "tool_call_id") else None
                if tc_id and tc_id not in tc_to_tool_idx:
                    tc_to_tool_idx[tc_id] = i  # take first

        # ── Second pass: convert with filter + reorder ──
        # Each item: (entry_dict, group_id)
        # group_id is non-None for assistant+tools that must stay together
        # (preserved by trim).
        history_with_groups: list[tuple[dict[str, Any], int | None]] = []
        compaction_count = 0
        emitted_assistant_idxs: set[int] = set()
        emitted_tool_msg_idxs: set[int] = set()

        for i, msg in enumerate(messages[:-1]):
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            parts = msg.metadata.get("_parts", []) if hasattr(msg, "metadata") else []
            message_type = msg.message_type if hasattr(msg, "message_type") else "assistant"

            # Handle compaction messages: filter then convert
            if message_type == "compaction":
                if i not in keep_compaction_indices:
                    continue  # skip older compactions
                compaction_count += 1
                logger.debug("[HIST] keeping compaction msg id=%s content_len=%d",
                           msg.message_id, len(content))
                comp = CompactionMessage(
                    id=msg.message_id,
                    session_id=msg.session_id,
                    summary=content,
                    recent="",
                    reason="auto",
                )
                history_with_groups.append((comp.to_llm_message(), None))
                continue

            if role == "tool":
                # Tool messages are emitted as part of their assistant.
                # Three cases:
                # 1. Already emitted alongside its assistant → skip
                # 2. Assistant comes later in the list → defer (we'll pair it then)
                # 3. Assistant not in this history slice (orphan) → drop with log
                if i in emitted_tool_msg_idxs:
                    continue
                tc_id = msg.tool_call_id if hasattr(msg, "tool_call_id") else None
                if not tc_id:
                    continue
                assistant_idx = tool_to_assistant_idx.get(tc_id)
                if assistant_idx is None or assistant_idx in emitted_assistant_idxs:
                    # Orphan or already-paired-but-skipped → drop
                    if assistant_idx is None:
                        logger.debug(
                            "[HIST] orphan tool dropped: tc_id=%s msg_id=%s",
                            tc_id, msg.message_id,
                        )
                    continue
                # Defer: assistant is later, will be paired then
                continue

            if role not in ("user", "assistant"):
                continue

            entry: dict[str, Any] = {"role": role, "content": content or ""}
            group_id: int | None = None

            if role == "assistant" and parts:
                tool_calls = []
                for p in parts:
                    if not isinstance(p, dict) or p.get("type") != "tool_call":
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
                    group_id = i  # this assistant + its tools share group_id

            if not content and not entry.get("tool_calls"):
                continue

            history_with_groups.append((entry, group_id))
            emitted_assistant_idxs.add(i)

            # Immediately follow with all matching tool messages in
            # their original (created_at) order. Tools may have been
            # seen earlier in the iteration (chronologically before
            # the assistant's final text) — that's fine, we pair them
            # here using tc_to_tool_idx.
            if role == "assistant" and entry.get("tool_calls"):
                seen_tc_ids: set[str] = set()
                for tc in entry["tool_calls"]:
                    tc_id = tc["id"]
                    if tc_id in seen_tc_ids:
                        continue
                    seen_tc_ids.add(tc_id)
                    tool_msg_idx = tc_to_tool_idx.get(tc_id)
                    if tool_msg_idx is None or tool_msg_idx in emitted_tool_msg_idxs:
                        continue
                    tool_msg = messages[tool_msg_idx]
                    tool_content = (
                        tool_msg.content if hasattr(tool_msg, "content")
                        else tool_msg.get("content", "")
                    )
                    tool_entry = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_content or "",
                    }
                    history_with_groups.append((tool_entry, group_id))
                    emitted_tool_msg_idxs.add(tool_msg_idx)

        # ── Trim by character budget from newest → oldest, preserving
        #    assistant-tool group integrity. ──
        # Group consecutive entries that share the same group_id;
        # each group is either kept whole or dropped whole. Within
        # a group, the order is preserved (assistant before its tools).
        grouped: list[list[tuple[dict[str, Any], int | None]]] = []
        current_group: list[tuple[dict[str, Any], int | None]] = []
        current_group_id: int | None = -1  # sentinel: no group yet
        for entry, gid in history_with_groups:
            if gid == current_group_id and gid is not None:
                current_group.append((entry, gid))
            else:
                if current_group:
                    grouped.append(current_group)
                current_group = [(entry, gid)]
                current_group_id = gid
        if current_group:
            grouped.append(current_group)

        total_chars = 0
        kept_groups: list[list[dict[str, Any]]] = []  # outer: groups in arrival order
        for group in reversed(grouped):
            group_chars = 0
            for e, _ in group:
                group_chars += len(e.get("content", ""))
                for tc in e.get("tool_calls") or []:
                    group_chars += len(tc.get("function", {}).get("arguments", ""))
            if total_chars + group_chars > MAX_HISTORY_CHARS:
                # Drop this whole group; older groups are even larger
                # relative to budget and also dropped
                continue
            kept_groups.append([e for e, _ in group])
            total_chars += group_chars
        # Reverse the outer group order (oldest group first), but keep
        # each group's internal order (assistant before tools).
        kept_groups.reverse()
        return [e for group in kept_groups for e in group]


# ── Helpers ────────────────────────────────────────────────────────────


def _accumulate_part(
    parts: list[dict[str, Any]],
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Accumulate SSE event payloads into a structured parts list.

    Mirrors the logic in chat.py:_run_agent_loop_background.

    Text events use the opencode-style 3-step lifecycle keyed by text_id:
        text.started { text_id }    → push new text part with id
        text_delta   { text_id, text } → findLast by id, append
        text.ended   { text_id, text } → findLast by id, override final

    text_delta events MUST carry text_id; missing text_id is a protocol
    error and the chunk is dropped.
    """
    if event_type == "text.started":
        text_id = data.get("text_id")
        if not text_id:
            logger.warning("text.started without text_id")
            return
        # Idempotent: SSE replay / duplicate emission. If a text part
        # with this id already exists, treat as a no-op.
        for p in reversed(parts):
            if p.get("type") == "text" and p.get("id") == text_id:
                return
        parts.append({"type": "text", "id": text_id, "text": ""})
    elif event_type == "text_delta":
        text_id = data.get("text_id")
        text = data.get("text", "")
        if not text_id:
            logger.warning("text_delta without text_id, dropping chunk")
            return
        for p in reversed(parts):
            if p.get("type") == "text" and p.get("id") == text_id:
                p["text"] += text
                break
        else:
            # Orphan: text.started hasn't arrived yet (replay / late join).
            logger.warning("text_delta with orphan text_id=%s, pushing new", text_id)
            parts.append({"type": "text", "id": text_id, "text": text})
    elif event_type == "text.ended":
        text_id = data.get("text_id")
        final_text = data.get("text", "")
        if not text_id:
            logger.warning("text.ended without text_id")
            return
        for p in reversed(parts):
            if p.get("type") == "text" and p.get("id") == text_id:
                p["text"] = final_text
                break
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


def _friendly_error_text(error_detail: str) -> str:
    """Map raw error detail to a user-friendly short message."""
    detail = (error_detail or "").lower()
    # MiniMax-specific: empty chat content (2013) usually means the
    # conversation history was over-compressed and the next LLM call
    # has no user content. Suggest creating a new session.
    if "2013" in detail or "chat content is empty" in detail:
        return "⚠️ 会话内容已压缩为空，请发送新消息或新建会话"
    # 400 + invalid params (e.g. malformed tool call or empty content)
    if "invalid params" in detail and "400" in detail:
        return "⚠️ 请求参数无效，请稍后重试或新建会话"
    if "rate" in detail or "429" in detail or "too many" in detail:
        return "⚠️ 模型请求频率过高，请稍后再试"
    if "timeout" in detail or "timed out" in detail:
        return "⚠️ 模型请求超时，请稍后再试"
    if "auth" in detail or "401" in detail or "403" in detail:
        return "⚠️ 模型鉴权失败，请检查 API Key 配置"
    if "quota" in detail or "balance" in detail:
        return "⚠️ 模型配额不足，请检查账户余额"
    if "server" in detail or "500" in detail or "502" in detail or "503" in detail:
        return "⚠️ 模型服务暂时不可用，请稍后再试"
    return "⚠️ 模型请求失败，请稍后再试"


__all__ = ["SessionService", "MAX_HISTORY_CHARS"]
