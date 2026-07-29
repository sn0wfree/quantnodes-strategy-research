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
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .events import EventBus
from .models import Attempt, AttemptStatus, Message
from .store import SessionStore

logger = logging.getLogger(__name__)

# Character budget for history (~3000 tokens) — borrowed verbatim from vibe_trading.
MAX_HISTORY_CHARS = 12000


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
        max_iterations: int = 5,
        system_prompt: Optional[str] = None,
        allow_shell_tools: bool = False,
    ) -> dict[str, str]:
        """Send a user message and trigger background AgentLoop execution.

        Args:
            session_id: Target session.
            content: User message text.
            model: Optional LLM model override (multi-model routing).
            max_iterations: AgentLoop iterations (default 1 for plain chat).
            system_prompt: Optional custom system prompt (TUI goal mode uses
                a different prompt than API chat mode).
            allow_shell_tools: Whether the registry may include shell tools.

        Returns:
            ``{"message_id": ..., "attempt_id": ...}`` for the user message
            and the spawned Attempt.
        """
        # 1. Persist user message — auto-generates UUID (don't reuse the
        #    attempt's message_id, which belongs to the assistant message
        #    so SSE text_delta events can find it).
        user_msg_id = self.store.append_message(
            Message(session_id=session_id, role="user", content=content),
        )

        # 2. Auto-title on first user message (preserve strategy-research feature).
        try:
            from ..routers.web_session import auto_title_session, _get_db

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

        # 3. Create Attempt and spawn background run.
        attempt = Attempt(
            session_id=session_id,
            prompt=content,
            message_id=str(uuid.uuid4()),  # assistant message ID for SSE correlation
            status=AttemptStatus.PENDING,
            created_at=_utc_now_iso(),
        )
        self.store.create_attempt(attempt)
        self.event_bus.emit(
            session_id,
            "attempt.created",
            {"attempt_id": attempt.attempt_id, "prompt": content},
        )

        # Unified message_received event (replaces both the old EventBus
        # "message.received" and the redundant sse_buffer.push in chat.py).
        # Carries BOTH message ids so the frontend can:
        #   - rename its optimistic user message placeholder to user_message_id
        #   - create the assistant placeholder directly with assistant_message_id
        # (without it, frontend has to guess which id to use and ends up
        # routing SSE text_delta events to the wrong message — the role-swap bug.)
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
                "status": "processing",
            },
        )

        task = asyncio.create_task(
            self._run_attempt(
                session_id=session_id,
                attempt=attempt,
                model=model,
                max_iterations=max_iterations,
                system_prompt=system_prompt,
                allow_shell_tools=allow_shell_tools,
            )
        )
        self._active_loops[attempt.attempt_id] = task
        task.add_done_callback(lambda t: self._active_loops.pop(attempt.attempt_id, None))

        return {
            "message_id": user_msg_id,
            "user_message_id": user_msg_id,
            "assistant_message_id": attempt.message_id,
            "attempt_id": attempt.attempt_id,
        }

    def cancel(self, attempt_id: str) -> bool:
        """Cancel an in-flight Attempt."""
        task = self._active_loops.get(attempt_id)
        if task is None:
            return False
        task.cancel()
        return True

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
        self.event_bus.emit(session_id, "attempt.started", {"attempt_id": attempt.attempt_id})

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
        def event_callback(event_type: str, data: dict[str, Any]) -> None:
            # Accumulate parts for persistence
            _accumulate_part(accumulated_parts, event_type, data)

            # Add attempt/message context
            data = dict(data)
            data.setdefault("attempt_id", attempt.attempt_id)
            data.setdefault("message_id", attempt.message_id)
            self.event_bus.emit(attempt.session_id, event_type, data)

        # Build AgentLoop
        agent = AgentLoop(
            config=cfg,
            registry=registry,
            workspace=Path.cwd(),
            on_event=event_callback,
            stream_mode=True,
            max_iterations=max_iterations,
            session_id=attempt.session_id,
            system_prompt=system_prompt,
            allowed_tools=None,
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
            "metrics": {},
        }

    # ── History conversion (borrowed verbatim from vibe_trading) ──────

    @staticmethod
    def _convert_messages_to_history(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Session messages to OpenAI-format history.

        Excludes the current turn (last message). Trims by character budget
        from the newest items so the LLM still sees the most recent context.

        Borrowed from vibe_trading ``SessionService._convert_messages_to_history``.
        """
        history: list[dict[str, Any]] = []
        for msg in messages[:-1]:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if not content or not content.strip():
                continue
            if role not in ("user", "assistant"):
                continue
            history.append({"role": role, "content": content})

        # Trim by character budget from newest → oldest.
        total_chars = 0
        trimmed: list[dict[str, Any]] = []
        for msg in reversed(history):
            msg_len = len(msg.get("content", ""))
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