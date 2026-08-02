"""AgentLoop: ReAct-style agent execution loop.

Minimal version (PR6-c2):
    - Builds initial messages via ContextBuilder
    - Calls LLM (OpenAICompatClient.chat)
    - Executes tool_calls in order
    - Returns LoopResult when LLM stops or max_iterations reached
    - Detects "no_progress" (last 3 tool_calls hashes identical)

Extended version (PR6-c3):
    - 3-layer context compression (microcompact + context_collapse)
    - HeartbeatTimer for long tool calls
    - TraceWriter integration (JSONL trace events)
    - git commit after run

NOT in this PR (PR7):
    - Tool dispatch optimizations
    - Cancellation tokens
    - Checkpointing

Exception handling policy:
    Agent loop and builtin tools use `except Exception` (BLE001) because
    any uncaught error in a tool or trace/memory helper would abort the
    loop. Failures are logged + traced + converted to error responses
    for the LLM. This is intentional and required for agent resilience.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..git import git_commit
from ..hooks.composite import CompositeHook
from ..hooks.context import AgentHookContext
from ..llm import LLMConfig, LLMResponse, OpenAICompatClient, ToolCall
from ..llm.errors import LLMError
from ..memory.persistent import PersistentMemory
from .compact import CompactConfig, compact_messages
from .context import ContextBuilder, estimate_tokens
from .progress import HeartbeatTimer
from .tools import ToolRegistry
from .trace import TraceWriter

logger = logging.getLogger(__name__)


# ── Compaction persistence registration ─────────────────────────────
#
# The web/API layer owns the sqlite schema, so the core loop must not
# import api.routers.web_session (layer inversion). Instead, process
# entry points (api app, TUI) register their persist_message wrapper
# here once at startup; the legacy fallback path below uses it.

_compaction_persister: Any | None = None


def register_compaction_persister(fn: Any) -> None:
    """Register a ``persist_message``-compatible callback (see web_session)."""
    global _compaction_persister
    _compaction_persister = fn


# ── Result dataclass ────────────────────────────────────────────────


@dataclass
class LoopResult:
    """Result of an AgentLoop run."""

    answer: str = ""
    iterations: int = 0
    tool_calls_made: int = 0
    finished_reason: str = "stop"     # stop | max_iter | no_progress | error
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    compression_applied: list[str] = field(default_factory=list)
    trace_path: str | None = None

    @property
    def success(self) -> bool:
        return self.finished_reason in ("stop", "max_iter") and bool(self.answer)


# ── Helpers ──────────────────────────────────────────────────────────


def _tool_call_hash(tc: ToolCall) -> str:
    """Stable hash for tool_call to detect no_progress."""
    payload = json.dumps({"name": tc.name, "arguments": tc.arguments},
                          sort_keys=True, ensure_ascii=False)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


# ── AgentLoop ────────────────────────────────────────────────────────


class AgentLoop:
    """ReAct agent loop.

    Usage:
        loop = AgentLoop(config=cfg, registry=registry, workspace=ws)
        result = loop.run("improve momentum_20_60")
        print(result.answer)
    """

    def __init__(
        self,
        config: LLMConfig,
        registry: ToolRegistry,
        memory: PersistentMemory | None = None,
        workspace: Path | None = None,
        max_iterations: int = 10,
        no_progress_window: int = 3,
        threshold_tokens: int | None = None,
        heartbeat_interval: float = 15.0,
        trace_dir: Path | None = None,
        auto_git_commit: bool = False,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        readonly: bool = False,
        session_id: str | None = None,
        strategy_name: str | None = None,
        enable_goal_injection: bool = True,
        enable_hypothesis_auto_create: bool = True,
        hooks: CompositeHook | None = None,
        session_manager: Any | None = None,
        on_event: Any | None = None,
        stream_mode: bool = True,
        compact_config: CompactConfig | None = None,
        event_bus: Any | None = None,
    ):
        self.config = config
        self.memory = memory
        self.workspace = workspace
        self.max_iterations = max_iterations
        self.no_progress_window = no_progress_window
        self.threshold_tokens = threshold_tokens
        self.heartbeat_interval = heartbeat_interval
        self.auto_git_commit = auto_git_commit
        self.session_id = session_id
        self.strategy_name = strategy_name
        self.enable_goal_injection = enable_goal_injection
        self.enable_hypothesis_auto_create = enable_hypothesis_auto_create
        self._hooks = hooks
        self._session_manager = session_manager
        self._on_event = on_event
        self._stream_mode = stream_mode
        self._event_bus = event_bus
        self.cc = compact_config or config.compact_config or CompactConfig()
        self._previous_summary: str | None = None

        # Tool filtering: allowed_tools > readonly > all
        if allowed_tools is not None:
            filtered = ToolRegistry()
            for name in allowed_tools:
                tool = registry.get(name)
                if tool is not None:
                    filtered.register(tool)
            self.registry = filtered
        elif readonly:
            filtered = ToolRegistry()
            for name, tool in registry._tools.items():
                if getattr(tool, "is_readonly", True):
                    filtered.register(tool)
            self.registry = filtered
        else:
            self.registry = registry

        self.context_builder = ContextBuilder(
            config=config, registry=self.registry,
            memory=memory, workspace=workspace,
            system_prompt=system_prompt,
            session_manager=getattr(self, '_session_manager', None),
        )
        self.client = OpenAICompatClient(config)
        # Track tool_calls per iteration for no_progress detection
        self._recent_hashes: list[str] = []
        # Trace writer (optional)
        self._trace_writer: TraceWriter | None = None
        if trace_dir is not None:
            self._trace_writer = TraceWriter(trace_dir)

    # ── Hook sync adapter ─────────────────────────

    def _fire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Sync adapter for async CompositeHook methods."""
        if self._hooks is None:
            return
        import asyncio
        try:
            method = getattr(self._hooks, method_name)
            coro = method(*args, **kwargs)
            if asyncio.iscoroutine(coro):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(coro)
                finally:
                    loop.close()
        except Exception:  # noqa: BLE001
            logger.warning("Hook %s failed", method_name, exc_info=True)

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        """Emit an event to the on_event callback (if set)."""
        if self._on_event is not None:
            try:
                self._on_event(event_type, data or {})
            except Exception:  # noqa: BLE001
                logger.warning("on_event callback failed for %s", event_type, exc_info=True)

    def emit_tool_progress(
        self,
        *,
        tool: str,
        call_id: str,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        message: str = "",
    ) -> None:
        """Emit a tool_progress event from inside a tool execution.

        Tools can call this to report in-flight progress (e.g. "downloaded
        chunk 3/10").  Stage names are tool-specific (e.g. "fetching",
        "parsing", "validating").
        """
        self._emit("tool_progress", {
            "tool": tool,
            "call_id": call_id,
            "stage": stage,
            "current": current,
            "total": total,
            "message": message,
        })

    def _stream_chat(self, messages: list[dict[str, Any]], iteration: int) -> Any:
        """Stream chat completion and emit text_delta events.

        Thinking/reasoning tokens are already extracted by the provider
        adapter into chunk.delta_thinking — we just forward them as
        thinking_delta SSE events.

        Text streaming uses an opencode-style 3-step protocol keyed by a
        per-iteration text_id:
            text.started { text_id }
            text_delta   { text_id, text }
            text.ended   { text_id, text }

        Each LLM iteration gets a fresh text_id; consumers can route
        deltas to the correct text part via findLast by id. This fixes
        the bug where text_delta streamed after a tool_call was appended
        to the FIRST text part of the message instead of a new one.

        Returns an LLMResponse-like object with content and tool_calls.
        """
        text_id = str(uuid.uuid4())
        self._emit("text.started", {"text_id": text_id})
        self._emit("thinking_start", {})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self.registry.get_definitions() or None
        try:
            for chunk in self.client.stream(messages, tools=tools):
                # Thinking tokens (extracted by provider adapter)
                if chunk.delta_thinking:
                    self._emit("thinking_delta", {"delta": chunk.delta_thinking})

                if chunk.delta_content:
                    if full_content == "" and chunk.delta_content:
                        # Transition: thinking_done before first text token
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                    })

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        idx = tc_delta.get("index", 0)
                        while len(accumulated_tool_calls) <= idx:
                            accumulated_tool_calls.append({
                                "id": tc_delta.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        tc = accumulated_tool_calls[idx]
                        if tc_delta.get("id"):
                            tc["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            tc["type"] = tc_delta["type"]
                        func_delta = tc_delta.get("function", {})
                        if func_delta.get("name"):
                            tc["function"]["name"] = func_delta["name"]
                        if func_delta.get("arguments"):
                            tc["function"]["arguments"] += func_delta["arguments"]

                if chunk.usage:
                    usage = chunk.usage
                    self._emit("llm_usage", chunk.usage)

                if chunk.finish_reason:
                    break
        except Exception:  # noqa: BLE001
            self._emit("thinking_end", {})
            self._emit("text.ended", {"text_id": text_id, "text": full_content})
            raise

        self._emit("thinking_end", {})
        self._emit("text.ended", {"text_id": text_id, "text": full_content})

        # Convert accumulated tool_calls to LLMResponse format
        from ..llm.parser import parse_chat_response
        raw_response: dict[str, Any] = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": tc["function"],
                        }
                        for tc in accumulated_tool_calls if tc["function"]["name"]
                    ] or None,
                },
                "finish_reason": "stop" if not accumulated_tool_calls else "tool_calls",
            }]
        }
        if usage:
            raw_response["usage"] = usage

        return parse_chat_response(raw_response, provider_name=self.config.provider)

    # ── Async helpers ─────────────────────────────

    async def _afire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Async version of _fire_hooks - awaits coroutine hooks directly."""
        if self._hooks is None:
            return
        try:
            method = getattr(self._hooks, method_name)
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            logger.warning("Hook %s failed", method_name, exc_info=True)

    async def _astream_chat(self, messages: list[dict[str, Any]], iteration: int) -> Any:
        """Async version of _stream_chat using client.astream().

        Uses the same 3-step text protocol as _stream_chat (text.started /
        text_delta{text_id} / text.ended). Each async LLM iteration gets
        a fresh text_id so the frontend can route deltas correctly even
        when text and tool calls interleave.
        """
        text_id = str(uuid.uuid4())
        self._emit("text.started", {"text_id": text_id})
        self._emit("thinking_start", {})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self.registry.get_definitions() or None
        try:
            async for chunk in self.client.astream(messages, tools=tools):
                if chunk.delta_content:
                    if full_content == "":
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                    })

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        idx = tc_delta.get("index", 0)
                        while len(accumulated_tool_calls) <= idx:
                            accumulated_tool_calls.append({
                                "id": tc_delta.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        tc_entry = accumulated_tool_calls[idx]
                        if tc_delta.get("id"):
                            tc_entry["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            tc_entry["type"] = tc_delta["type"]
                        func_delta = tc_delta.get("function", {})
                        if func_delta.get("name"):
                            tc_entry["function"]["name"] = func_delta["name"]
                        if func_delta.get("arguments"):
                            tc_entry["function"]["arguments"] += func_delta["arguments"]

                if chunk.usage:
                    usage = chunk.usage
                    self._emit("llm_usage", chunk.usage)

                if chunk.finish_reason:
                    break
        except Exception:  # noqa: BLE001
            self._emit("thinking_end", {})
            self._emit("text.ended", {"text_id": text_id, "text": full_content})
            raise

        self._emit("thinking_end", {})
        self._emit("text.ended", {"text_id": text_id, "text": full_content})

        from ..llm.parser import parse_chat_response
        raw_response: dict[str, Any] = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": tc["function"],
                        }
                        for tc in accumulated_tool_calls if tc["function"]["name"]
                    ] or None,
                },
                "finish_reason": "stop" if not accumulated_tool_calls else "tool_calls",
            }]
        }
        if usage:
            raw_response["usage"] = usage

        return parse_chat_response(raw_response, provider_name=self.config.provider)

    def _build_hook_context(
        self, iteration: int, messages: list[dict[str, Any]],
    ) -> AgentHookContext:
        """Build AgentHookContext for the current iteration."""
        return AgentHookContext(iteration=iteration, messages=messages)

    # ── Shared logic (sync-safe: pure logic + trace + emit, no I/O) ──

    def _prepare_run(
        self, task: str, context: str | None,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[str, LoopResult, list[dict[str, Any]], float]:
        """Assemble full_task, init result/messages, emit loop_start trace."""
        self._maybe_auto_create_hypothesis(task)
        goal_context = self._get_goal_context()
        full_task = task
        if context:
            full_task = context + "\n\n" + task
        if goal_context:
            full_task = goal_context + "\n\n" + full_task
        result = LoopResult()
        messages = self.context_builder.build_initial_messages(full_task, history=history)
        result.messages = list(messages)
        t0 = time.perf_counter()
        self._trace({
            "type": "loop_start",
            "task": task,
            "max_iterations": self.max_iterations,
            "tokens": estimate_tokens(messages),
        })
        return full_task, result, messages, t0

    def _emit_compaction(
        self, applied: list[str], iteration: int, result: LoopResult,
    ) -> None:
        """Extend compression_applied, emit compression trace + compact events."""
        result.compression_applied.extend(applied)
        self._trace({"type": "compression", "applied": applied, "iteration": iteration})
        for layer in applied:
            self._emit("compact", {
                "layer": layer,
                "iteration": iteration,
                "summary": f"Context compression: {layer}",
            })

    def _emit_iter_start(self, iteration: int, messages: list[dict[str, Any]]) -> None:
        """Emit iter_start trace + event, set _current_iter."""
        self._trace({"type": "iter_start", "iteration": iteration, "tokens": estimate_tokens(messages)})
        self._emit("iter_start", {"iteration": iteration, "max_iterations": self.max_iterations})
        self._current_iter = iteration

    def _handle_llm_error(
        self, exc: LLMError, iteration: int, result: LoopResult,
    ) -> None:
        """Populate error result fields, emit error trace + event."""
        result.finished_reason = "error"
        result.error = f"{type(exc).__name__}: {exc}"
        self._trace({"type": "error", "iteration": iteration, "error": str(exc)})
        self._emit("error", {"message": str(exc), "fatal": True})

    @staticmethod
    def _is_stream_required_error(exc: Exception) -> bool:
        """Whether the error should NOT trigger a stream→achat fallback.

        Auth/rate-limit/config errors are equally fatal for both modes
        (return True → no fallback). Everything else (timeout, server,
        malformed SSE, JSON parse errors) gets one ``achat()`` retry.
        """
        from ..llm.errors import (
            LLMAuthError,
            LLMConfigError,
            LLMQuotaError,
            LLMRateLimitError,
        )
        return isinstance(exc, (LLMAuthError, LLMRateLimitError, LLMConfigError, LLMQuotaError))

    def _append_assistant_msg(
        self, response: LLMResponse, messages: list[dict[str, Any]],
        result: LoopResult, iteration: int,
    ) -> None:
        """Convert response to assistant msg, append to messages/result, trace."""
        assistant_msg = self._response_to_assistant_msg(response)
        messages.append(assistant_msg)
        result.messages.append(assistant_msg)
        self._trace({
            "type": "llm_response",
            "iteration": iteration,
            "finish_reason": response.finish_reason,
            "has_tool_calls": response.has_tool_calls(),
            "tool_call_count": len(response.tool_calls),
            "content_preview": (response.content or "")[:200],
        })

    def _check_goal_continuation(
        self, response: LLMResponse, messages: list[dict[str, Any]],
        result: LoopResult, iteration: int,
    ) -> bool:
        """Check if goal needs continuation; inject prompt if so. Return True if continuing."""
        goal_snapshot = self._get_goal_snapshot()
        if goal_snapshot is None:
            return False
        from ..goal.context import (
            format_goal_continuation_prompt,
            goal_needs_continuation,
        )
        if not goal_needs_continuation(goal_snapshot):
            return False
        continuation = format_goal_continuation_prompt(
            goal_snapshot, previous_answer=response.content or "",
        )
        messages.append({"role": "user", "content": continuation})
        result.messages.append({"role": "user", "content": continuation})
        self._trace({
            "type": "goal_continuation",
            "iteration": iteration,
            "goal_id": goal_snapshot.get("goal", {}).get("goal_id", ""),
        })
        return True

    def _collect_tool_hashes(
        self, tool_calls: list[ToolCall], tool_result_msgs: list[dict[str, Any]],
    ) -> list[str]:
        """Compute tool_call hashes for no-progress detection."""
        return [_tool_call_hash(tc) for tc in tool_calls]

    def _append_tool_results(
        self,
        tool_calls: list[ToolCall],
        tool_result_msgs: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        result: LoopResult,
    ) -> None:
        """Append tool result messages to messages and result.messages."""
        for tool_result_msg in tool_result_msgs:
            messages.append(tool_result_msg)
            result.messages.append(tool_result_msg)

    def _check_no_progress(
        self, tool_hashes: list[str], response: LLMResponse,
        result: LoopResult, iteration: int,
    ) -> bool:
        """Update _recent_hashes, detect no_progress. If triggered, fill result + emit. Return True if triggered."""
        self._recent_hashes.extend(tool_hashes)
        if len(self._recent_hashes) > self.no_progress_window:
            self._recent_hashes = self._recent_hashes[-self.no_progress_window:]
        if not self._detect_no_progress():
            return False
        result.finished_reason = "no_progress"
        result.answer = (
            response.content or
            f"No progress detected (last {self.no_progress_window} tool calls identical)"
        )
        self._trace({"type": "loop_end", "reason": "no_progress", "iteration": iteration})
        self._emit("assistant_message", {"content": result.answer})
        self._emit("iter_end", {
            "iteration": iteration,
            "finish_reason": "no_progress",
            "tool_calls_made": result.tool_calls_made,
        })
        return True

    def _handle_max_iter(self, result: LoopResult) -> None:
        """Populate max_iter result fields, emit trace + event."""
        result.finished_reason = "max_iter"
        if not result.answer:
            result.answer = (
                f"Reached max_iterations={self.max_iterations} without a final answer."
            )
        self._trace({"type": "loop_end", "reason": "max_iter", "iteration": result.iterations})
        self._emit("assistant_message", {"content": result.answer})
        self._emit("iter_end", {
            "iteration": result.iterations,
            "finish_reason": "max_iter",
            "tool_calls_made": result.tool_calls_made,
        })

    def _handle_stop(
        self, response: LLMResponse, result: LoopResult, iteration: int,
    ) -> None:
        """Populate stop result fields, emit trace + event."""
        result.answer = response.content
        result.finished_reason = "stop"
        self._trace({"type": "loop_end", "reason": "stop", "iteration": iteration})
        self._emit("assistant_message", {"content": response.content or ""})
        self._emit("iter_end", {
            "iteration": iteration,
            "finish_reason": "stop",
            "tool_calls_made": result.tool_calls_made,
        })

    def _finalize_metrics(
        self, result: LoopResult, messages: list[dict[str, Any]], t0: float,
    ) -> None:
        """Populate elapsed/tokens metrics, emit loop_final trace."""
        elapsed = time.perf_counter() - t0
        result.metrics["elapsed_s"] = round(elapsed, 2)
        result.metrics["tokens"] = estimate_tokens(messages)
        self._trace({
            "type": "loop_final",
            "reason": result.finished_reason,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
            "elapsed_s": round(elapsed, 2),
            "compression": result.compression_applied,
        })

    # ── Public API ───────────────────────────────

    def run(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Run the loop until done.

        Args:
            task: User task description.
            context: Optional context to prepend to task (e.g., current_state).
            history: Optional prior conversation turns in OpenAI
                ``{"role": ..., "content": ...}`` format. Inserted between
                the system prompt and the current user message so the LLM
                has full conversation context.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        logger.info("[AGENT] run start task_len=%d history_len=%d",
                   len(task), len(history) if history else 0)

        # Log compaction messages in history
        if history:
            compaction_count = sum(1 for h in history
                                  if h.get("role") == "user"
                                  and "<conversation-checkpoint>" in h.get("content", ""))
            logger.info("[AGENT] compaction_in_history=%d", compaction_count)

        full_task, result, messages, t0 = self._prepare_run(task, context, history)
        hook_ctx = self._build_hook_context(0, messages)
        self._fire_hooks("before_run", hook_ctx)

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            hook_ctx = self._build_hook_context(iteration, messages)
            self._fire_hooks("before_iteration", hook_ctx)

            messages, applied = self._maybe_compact(messages)
            if applied:
                self._emit_compaction(applied, iteration, result)
            self._emit_iter_start(iteration, messages)

            try:
                if self._stream_mode:
                    response = self._stream_chat(messages, iteration)
                else:
                    tools = self.registry.get_definitions() or None
                    response = self.client.chat(messages, tools=tools)
            except LLMError as exc:
                if self._stream_mode and not self._is_stream_required_error(exc):
                    try:
                        tools = self.registry.get_definitions() or None
                        response = self.client.chat(messages, tools=tools)
                    except LLMError as exc2:
                        self._handle_llm_error(exc2, iteration, result)
                        self._fire_hooks("on_error", hook_ctx, exc2)
                        break
                else:
                    self._handle_llm_error(exc, iteration, result)
                    self._fire_hooks("on_error", hook_ctx, exc)
                    break

            self._append_assistant_msg(response, messages, result, iteration)

            if not response.has_tool_calls():
                if self._check_goal_continuation(response, messages, result, iteration):
                    continue
                self._handle_stop(response, result, iteration)
                self._fire_hooks("after_iteration", hook_ctx)
                break

            self._fire_hooks("before_execute_tools", hook_ctx)
            tool_result_msgs = self._execute_tool_batch(response.tool_calls, result)
            tool_hashes = self._collect_tool_hashes(response.tool_calls, tool_result_msgs)
            for tc, tool_result_msg in zip(response.tool_calls, tool_result_msgs):
                if tool_result_msg.get("content", "").startswith('{"status": "error"'):
                    self._fire_hooks("on_tool_error", hook_ctx, tc, RuntimeError(tool_result_msg["content"]))
                else:
                    self._fire_hooks("after_tool_executed", hook_ctx, tc, tool_result_msg)
            self._append_tool_results(response.tool_calls, tool_result_msgs, messages, result)
            self._fire_hooks("after_iteration", hook_ctx)

            if self._check_no_progress(tool_hashes, response, result, iteration):
                self._fire_hooks("after_run", hook_ctx, result)
                return result
        else:
            self._handle_max_iter(result)

        self._finalize_metrics(result, messages, t0)
        self._fire_hooks("after_run", hook_ctx, result)
        self._git_commit(full_task, result)

        if self._trace_writer is not None:
            result.trace_path = str(self._trace_writer.path)

        return result

    # ── Async public API ──────────────────────────

    async def arun(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Async version of run() - all I/O points use await.

        Uses ``client.astream()`` / ``client.achat()`` for LLM calls,
        ``asyncio.to_thread()`` for tool execution, and ``asyncio.gather()``
        for parallel readonly tools.

        Args:
            task: The user's task description.
            context: Optional goal-context prefix prepended to the task.
            history: Optional prior conversation turns in OpenAI
                ``{"role": ..., "content": ...}`` format. Inserted between
                the system prompt and the current user message so the LLM
                has full conversation context.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        full_task, result, messages, t0 = self._prepare_run(task, context, history)
        hook_ctx = self._build_hook_context(0, messages)
        await self._afire_hooks("before_run", hook_ctx)

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            hook_ctx = self._build_hook_context(iteration, messages)
            await self._afire_hooks("before_iteration", hook_ctx)

            messages, applied = await self._amaybe_compact(messages)
            if applied:
                self._emit_compaction(applied, iteration, result)
            self._emit_iter_start(iteration, messages)

            try:
                if self._stream_mode:
                    response = await self._astream_chat(messages, iteration)
                else:
                    tools = self.registry.get_definitions() or None
                    response = await self.client.achat(messages, tools=tools)
            except LLMError as exc:
                if self._stream_mode and not self._is_stream_required_error(exc):
                    # Streaming failed for non-streaming-required reasons
                    # (e.g. provider doesn't support SSE, parsing error on
                    # a partial chunk). Fall back to non-streaming achat().
                    try:
                        tools = self.registry.get_definitions() or None
                        response = await self.client.achat(messages, tools=tools)
                    except LLMError as exc2:
                        self._handle_llm_error(exc2, iteration, result)
                        await self._afire_hooks("on_error", hook_ctx, exc2)
                        break
                else:
                    self._handle_llm_error(exc, iteration, result)
                    await self._afire_hooks("on_error", hook_ctx, exc)
                    break

            self._append_assistant_msg(response, messages, result, iteration)

            if not response.has_tool_calls():
                if self._check_goal_continuation(response, messages, result, iteration):
                    continue
                self._handle_stop(response, result, iteration)
                await self._afire_hooks("after_iteration", hook_ctx)
                break

            await self._afire_hooks("before_execute_tools", hook_ctx)
            tool_result_msgs = await self._aexecute_tool_batch(response.tool_calls, result)
            tool_hashes = self._collect_tool_hashes(response.tool_calls, tool_result_msgs)
            for tc, tool_result_msg in zip(response.tool_calls, tool_result_msgs):
                if tool_result_msg.get("content", "").startswith('{"status": "error"'):
                    await self._afire_hooks("on_tool_error", hook_ctx, tc, RuntimeError(tool_result_msg["content"]))
                else:
                    await self._afire_hooks("after_tool_executed", hook_ctx, tc, tool_result_msg)
            self._append_tool_results(response.tool_calls, tool_result_msgs, messages, result)
            await self._afire_hooks("after_iteration", hook_ctx)

            if self._check_no_progress(tool_hashes, response, result, iteration):
                await self._afire_hooks("after_run", hook_ctx, result)
                return result
        else:
            self._handle_max_iter(result)

        self._finalize_metrics(result, messages, t0)
        await self._afire_hooks("after_run", hook_ctx, result)
        await asyncio.to_thread(self._git_commit, full_task, result)

        if self._trace_writer is not None:
            result.trace_path = str(self._trace_writer.path)

        return result

    # ── Internal helpers ─────────────────────────

    def _response_to_assistant_msg(self, response: LLMResponse) -> dict[str, Any]:
        """Convert LLMResponse to an assistant message dict."""
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": response.content,
        }
        if response.has_tool_calls():
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    def _execute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute one tool_call via the registry; return tool-result message."""
        result.tool_calls_made += 1
        tool = self.registry.get(tc.name)
        if tool is None:
            logger.warning("tool '%s' not in registry", tc.name)
            self._trace({"type": "tool_error", "tool": tc.name, "error": "not in registry"})
            self._emit("tool_result", {
            "tool": tc.name,
            "call_id": tc.id,
            "status": "error",
            "ok": False,  # backward compat
            "elapsed_ms": 0,
            "preview": "tool not in registry",
        })
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    {"status": "error", "error": f"tool '{tc.name}' not found"},
                    ensure_ascii=False,
                ),
            }

        # Emit tool_call event
        self._emit("tool_call", {
            "tool": tc.name,
            "name": tc.name,            # frontend reads data.name
            "id": tc.id,                # frontend reads data.id
            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
            if not isinstance(tc.arguments, str)
            else tc.arguments,
            "call_id": tc.id,           # backward compat
            "iter": getattr(self, "_current_iter", 0),
        })

        # Inject workspace kwarg if not present
        kwargs = dict(tc.arguments)
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace

        # Inject progress callback so tools can report progress steps
        def _progress_callback(steps: list[str]) -> None:
            self._emit("tool_progress", {
                "id": tc.id,
                "steps": steps,
            })
        kwargs["_progress_callback"] = _progress_callback

        t0 = time.perf_counter()
        try:
            output = tool.execute(**kwargs)
        except Exception as exc:                    # noqa: BLE001
            logger.exception("tool %s raised", tc.name)
            output = json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Emit tool_result event
        is_error = isinstance(output, str) and output.startswith('{"status": "error"')
        status_str = "error" if is_error else "done"
        output_preview = (output[:200] if isinstance(output, str) else str(output))[:200]
        self._emit("tool_result", {
            "tool": tc.name,
            "id": tc.id,                # frontend reads data.id
            "call_id": tc.id,           # backward compat
            "status": status_str,
            "ok": not is_error,          # backward compat
            "result": output_preview,    # frontend reads data.result
            "preview": output_preview,   # backward compat
            "elapsed_ms": elapsed_ms,
        })

        # Trace tool result
        self._trace({
            "type": "tool_result",
            "tool": tc.name,
            "call_id": tc.id,
            "elapsed_ms": elapsed_ms,
            "output_preview": output_preview,
        })

        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        }

    def _execute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute tool_call with HeartbeatTimer for long-running tools."""
        def _heartbeat_tick(payload: dict) -> None:
            self._trace({"type": "heartbeat", **payload})
            self._emit("tool_heartbeat", {
                "tool": tc.name,
                "call_id": tc.id,
                "elapsed_s": payload.get("elapsed_s", 0.0),
            })

        with HeartbeatTimer(
            tool_name=tc.name,
            interval=self.heartbeat_interval,
            emit=_heartbeat_tick,
        ):
            return self._execute_tool_call(tc, result)

    def _execute_tool_batch(
        self, tool_calls: list[ToolCall], result: LoopResult
    ) -> list[dict[str, Any]]:
        """Execute tool_calls with read-only parallelism.

        Read-only tools are dispatched in parallel via ThreadPoolExecutor.
        Write tools (is_readonly=False) run serially to prevent races.
        Results are returned in the same order as tool_calls.
        """
        if not tool_calls:
            return []

        # Single tool: no batching overhead
        if len(tool_calls) == 1:
            return [self._execute_tool_with_heartbeat(tool_calls[0], result)]

        # Classify each tool_call as readonly or write
        readonly_indices: list[int] = []
        write_indices: list[int] = []
        for i, tc in enumerate(tool_calls):
            tool = self.registry.get(tc.name)
            if tool is not None and not getattr(tool, "is_readonly", True):
                write_indices.append(i)
            else:
                readonly_indices.append(i)

        # Prepare result slots (preserves order)
        results: list[dict[str, Any] | None] = [None] * len(tool_calls)

        # Dispatch readonly tools in parallel
        if readonly_indices:
            max_workers = min(len(readonly_indices), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(self._execute_tool_with_heartbeat, tool_calls[i], result): i
                    for i in readonly_indices
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("parallel tool %s failed", tool_calls[idx].name)
                        results[idx] = {
                            "role": "tool",
                            "tool_call_id": tool_calls[idx].id,
                            "content": json.dumps(
                                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                                ensure_ascii=False,
                            ),
                        }

        # Dispatch write tools serially
        for i in write_indices:
            results[i] = self._execute_tool_with_heartbeat(tool_calls[i], result)

        return [r for r in results if r is not None]

    def _detect_no_progress(self) -> bool:
        """Return True if last N tool_calls all have the same hash."""
        if len(self._recent_hashes) < self.no_progress_window:
            return False
        window = self._recent_hashes[-self.no_progress_window:]
        return len(set(window)) == 1

    # ── Async tool execution ─────────────────────

    async def _aexecute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_call using asyncio.to_thread."""
        result.tool_calls_made += 1
        tool = self.registry.get(tc.name)
        if tool is None:
            logger.warning("tool '%s' not in registry", tc.name)
            self._trace({"type": "tool_error", "tool": tc.name, "error": "not in registry"})
            self._emit("tool_result", {
                "tool": tc.name,
                "id": tc.id,
                "call_id": tc.id,
                "status": "error",
                "ok": False,
                "result": "tool not in registry",
                "preview": "tool not in registry",
                "elapsed_ms": 0,
            })
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    {"status": "error", "error": f"tool '{tc.name}' not found"},
                    ensure_ascii=False,
                ),
            }

        self._emit("tool_call", {
            "tool": tc.name,
            "name": tc.name,
            "id": tc.id,
            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
            if not isinstance(tc.arguments, str)
            else tc.arguments,
            "call_id": tc.id,
            "iter": getattr(self, "_current_iter", 0),
        })

        kwargs = dict(tc.arguments)
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace

        t0 = time.perf_counter()
        try:
            output = await asyncio.to_thread(tool.execute, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s raised", tc.name)
            output = json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        is_error = isinstance(output, str) and output.startswith('{"status": "error"')
        status_str = "error" if is_error else "done"
        output_preview = (output[:200] if isinstance(output, str) else str(output))[:200]
        self._emit("tool_result", {
            "tool": tc.name,
            "id": tc.id,
            "call_id": tc.id,
            "status": status_str,
            "ok": not is_error,
            "result": output_preview,
            "preview": output_preview,
            "elapsed_ms": elapsed_ms,
        })

        self._trace({
            "type": "tool_result",
            "tool": tc.name,
            "call_id": tc.id,
            "elapsed_ms": elapsed_ms,
            "output_preview": output_preview,
        })

        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        }

    async def _aexecute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_with_heartbeat."""
        def _heartbeat_tick(payload: dict) -> None:
            self._trace({"type": "heartbeat", **payload})
            self._emit("tool_heartbeat", {
                "tool": tc.name,
                "call_id": tc.id,
                "elapsed_s": payload.get("elapsed_s", 0.0),
            })

        with HeartbeatTimer(
            tool_name=tc.name,
            interval=self.heartbeat_interval,
            emit=_heartbeat_tick,
        ):
            return await self._aexecute_tool_call(tc, result)

    async def _aexecute_tool_batch(
        self, tool_calls: list[ToolCall], result: LoopResult
    ) -> list[dict[str, Any]]:
        """Async version of _execute_tool_batch using asyncio.gather for readonly."""
        if not tool_calls:
            return []

        if len(tool_calls) == 1:
            return [await self._aexecute_tool_with_heartbeat(tool_calls[0], result)]

        readonly_indices: list[int] = []
        write_indices: list[int] = []
        for i, tc in enumerate(tool_calls):
            tool = self.registry.get(tc.name)
            if tool is not None and not getattr(tool, "is_readonly", True):
                write_indices.append(i)
            else:
                readonly_indices.append(i)

        results: list[dict[str, Any] | None] = [None] * len(tool_calls)

        if readonly_indices:
            coros = [
                self._aexecute_tool_with_heartbeat(tool_calls[i], result)
                for i in readonly_indices
            ]
            gathered = await asyncio.gather(*coros, return_exceptions=True)
            for idx, res in zip(readonly_indices, gathered):
                if isinstance(res, Exception):
                    logger.exception("parallel tool %s failed", tool_calls[idx].name)
                    results[idx] = {
                        "role": "tool",
                        "tool_call_id": tool_calls[idx].id,
                        "content": json.dumps(
                            {"status": "error", "error": f"{type(res).__name__}: {res}"},
                            ensure_ascii=False,
                        ),
                    }
                else:
                    results[idx] = res

        for i in write_indices:
            results[i] = await self._aexecute_tool_with_heartbeat(tool_calls[i], result)

        return [r for r in results if r is not None]

    # ── Context compression ─────────────────────────

    def _maybe_compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply context compression if over threshold.

        3-layer progressive compression (opencode-aligned):
            L1 (microcompact_ratio=0.9): Smart microcompact
            L4 (llm_summarize_ratio=0.95): LLM-driven summary
            L3 (hard_truncate_ratio=0.99): Hard truncate (rare)

        Also checks overflow (overflow_ratio=0.99) to force
        compression when near context limit.

        opencode-aligned trigger formula:
            trigger = threshold_tokens (default derived from model
            context: context - max(model_max_output, buffer))

        If L4 fails (e.g. DB error during persistence), the
        compaction is rolled back and the original messages are
        kept. The LLM doesn't lose context.
        """
        # Overflow detection: log only (no force compact in this path)
        if self.config.model_context_tokens:
            usable = self.config.model_context_tokens - 4096
            tokens = estimate_tokens(messages)
            if tokens >= usable * self.cc.overflow_ratio:
                logger.debug(
                    "Overflow detected: %d tokens >= %d usable * %.2f",
                    tokens, usable, self.cc.overflow_ratio,
                )

        # Save original for rollback. compact_messages reassigns `messages`
        # in the loop, so we need a reference to the pre-compaction list.
        original_messages = list(messages)

        # Delegate to compact_messages engine
        # opencode-aligned: returns 4-tuple (messages, applied, summary, recent_text).
        # The summary text + recent text are pre-computed in compact and
        # passed through to the persistence step. No more NoneType risk
        # in the loop (the recent_count // 100 bug is gone).
        try:
            messages, applied, l4_summary_text, l4_recent_text = compact_messages(
                messages,
                config=self.cc,
                threshold_tokens=self.threshold_tokens,
                model_context_tokens=self.config.model_context_tokens,
                model_max_output_tokens=self.config.model_max_output_tokens,
                llm_client=self.client,
                previous_summary=self._previous_summary,
                session_id=self.session_id,
            )
        except Exception:
            # Critical: L4 failed. Roll back to keep full history.
            # The LLM is more useful with full history than with
            # partial compaction.
            logger.exception("L4 compaction failed; keeping full history")
            return original_messages, []

        if l4_summary_text and any(layer.startswith("llm_summarize") for layer in applied):
            self._previous_summary = l4_summary_text
            try:
                self._persist_compaction_event(l4_summary_text, l4_recent_text or "")
            except Exception:
                # Persistence failed AFTER L4 generated summary.
                # Roll back to original messages so the LLM keeps
                # full history on the next turn.
                logger.exception(
                    "compaction persistence failed; rolling back to original messages",
                )
                return original_messages, []

        return messages, applied

    def _persist_compaction_event(
        self,
        summary_text: str,
        recent_text: str,
    ) -> None:
        """Persist a CompactionMessage event for the L4 layer.

        B6: Event-sourced path. Emits a compact.ended event via the
        injected EventBusV2. The projector handles materialization
        to messages + message_parts tables.

        Falls back to direct persist_message if no event_bus is
        injected (for legacy code paths that haven't migrated yet).

        Args:
            summary_text: LLM-generated summary (non-empty).
            recent_text: Pre-serialized recent messages from compact.

        Raises:
            Exception: propagates critical errors (not silent fail).
        """
        session_id = self.session_id
        if not session_id:
            logger.debug("L4 ran but no session_id; skipping compaction persistence")
            return

        if not summary_text or not summary_text.strip():
            logger.warning("L4 returned empty summary; skipping persistence")
            return

        try:
            from .compaction_message import new_compaction_message

            comp = new_compaction_message(
                session_id=session_id,
                summary=summary_text,
                recent=recent_text or "",
                reason="auto",
            )

            if self._event_bus is not None:
                # B6: Event-sourced path. Emit compact.ended event.
                # Note: L4 auto-compaction is a "compaction happened"
                # marker — it does NOT replace existing history. So
                # we do NOT include the 'messages' field (which the
                # projector interprets as a replacement set used by
                # /compact manual command).
                # The projector creates a single compaction marker
                # message from the summary.
                self._event_bus.emit(
                    session_id,
                    "compact.ended",
                    {
                        "summary": comp.summary,
                        "reason": "auto",
                        "compaction_id": comp.id,
                        "metadata": comp.metadata,
                    },
                )
                logger.info(
                    "compaction event emitted: %s (summary=%d chars, recent=%d chars)",
                    comp.id, len(comp.summary), len(comp.recent),
                )
            else:
                # Legacy fallback: direct DB write via the registered
                # persister (registered by api/ and TUI entry points).
                # Without a registration, compaction persistence is
                # skipped with a warning instead of importing the API
                # layer from core.
                if _compaction_persister is None:
                    logger.warning(
                        "No compaction persister registered; skipping "
                        "legacy DB write for %s", comp.id,
                    )
                else:
                    _compaction_persister(
                        session_id=comp.session_id,
                        role="assistant",  # DB compat
                        content=comp.summary,
                        parts=comp.to_parts(),
                        message_id=comp.id,
                        message_type="compaction",
                    )
                    logger.info(
                        "compaction event persisted (legacy): %s (summary=%d chars, recent=%d chars)",
                        comp.id, len(comp.summary), len(comp.recent),
                    )
        except Exception:
            # Critical: propagate with full traceback. The caller
            # (compact_messages path) will see the error and roll
            # back messages to keep full history intact.
            logger.exception("compaction persistence failed")
            raise

    async def _amaybe_compact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Async version of _maybe_compact — uses sync compact_messages with achat fallback for L4."""
        # Overflow detection
        if self.config.model_context_tokens:
            usable = self.config.model_context_tokens - 4096
            tokens = estimate_tokens(messages)
            if tokens >= usable * self.cc.overflow_ratio:
                logger.debug("Overflow detected (async): %d tokens", tokens)

        # For async path, use compact_messages with achat-wrapped client
        class _AchatAdapter:
            """Wraps self.client.achat as sync .chat() for compact_messages."""
            def __init__(self, client: Any) -> None:
                self._client = client
            def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    # We're inside the running event loop (async agent
                    # path); a blocking sync LLM call here would freeze
                    # the whole server. Run it in a worker thread.
                    return asyncio.run(
                        asyncio.to_thread(self._client.chat, messages, **kwargs)
                    )
                return asyncio.run(self._client.achat(messages, **kwargs))

        adapter = _AchatAdapter(self.client)
        original_messages = list(messages)
        try:
            messages, applied, l4_summary_text, l4_recent_text = compact_messages(
                messages,
                config=self.cc,
                threshold_tokens=self.threshold_tokens,
                model_context_tokens=self.config.model_context_tokens,
                model_max_output_tokens=self.config.model_max_output_tokens,
                llm_client=adapter,
                previous_summary=self._previous_summary,
                session_id=self.session_id,
            )
        except Exception:
            logger.exception("L4 compaction failed (async); keeping full history")
            return original_messages, []

        if l4_summary_text and any(layer.startswith("llm_summarize") for layer in applied):
            self._previous_summary = l4_summary_text
            try:
                self._persist_compaction_event(l4_summary_text, l4_recent_text or "")
            except Exception:
                logger.exception(
                    "compaction persistence failed (async); rolling back",
                )
                return original_messages, []

        return messages, applied

    # ── Trace helpers ──────────────────────────────

    def _trace(self, entry: dict[str, Any]) -> None:
        """Write a trace entry if trace writer is active."""
        if self._trace_writer is not None:
            try:
                self._trace_writer.write(entry)
            except Exception:                       # noqa: BLE001
                pass  # trace failures should never break the loop

    # ── P3-d: Goal + Hypothesis integration ────────

    def _maybe_auto_create_hypothesis(self, task: str) -> None:
        """Auto-create an exploring hypothesis per (strategy, market) on first run.

        Per the P3-b user decision, this fires only when:
          - enable_hypothesis_auto_create is True (default)
          - session_id is set
          - strategy_name is set
          - registry has no matching (strategy, market) hypothesis yet

        Failures are swallowed (logged at most) to avoid breaking the loop.
        """
        if not self.enable_hypothesis_auto_create:
            return
        if not self.session_id or not self.strategy_name:
            return
        try:
            from ..hypothesis import HypothesisAutoCreator
            creator = HypothesisAutoCreator()
            hyp = creator.maybe_auto_create(
                session_id=self.session_id,
                strategy_name=self.strategy_name,
                initial_thesis=task,
                # FIXME: hardcoded market. The unified market detection
                # (core/utils/market_detection.py) was standardized in
                # Phase 4-3; this call site should resolve the market
                # from the detected asset universe instead of assuming
                # A-shares.
                market="a_share",
            )
            if hyp is not None:
                self._trace({
                    "type": "hypothesis_auto_created",
                    "hypothesis_id": hyp.hypothesis_id,
                    "title": hyp.title,
                })
        except Exception as exc:                     # noqa: BLE001
            # Never let hypothesis machinery break the agent loop
            self._trace({
                "type": "hypothesis_auto_create_failed",
                "error": str(exc),
            })

    def _get_goal_context(self) -> str:
        """Return formatted <current-research-goal> block for this session.

        Returns empty string when:
          - enable_goal_injection is False
          - no session_id is set
          - no current goal exists for the session

        Failures are swallowed to avoid breaking the loop.
        """
        if not self.enable_goal_injection:
            return ""
        if not self.session_id:
            return ""
        try:
            from ..goal import get_current_goal_context
            ctx, _ = get_current_goal_context(self.session_id)
            return ctx
        except Exception as exc:                     # noqa: BLE001
            self._trace({
                "type": "goal_context_failed",
                "error": str(exc),
            })
            return ""

    def _get_goal_snapshot(self) -> dict[str, Any] | None:
        """Return the raw goal snapshot for continuation checks.

        Returns None when no active goal exists or on failure.
        """
        if not self.enable_goal_injection:
            return None
        if not self.session_id:
            return None
        try:
            from ..goal.store import GoalStore
            return GoalStore().get_current_snapshot(self.session_id)
        except Exception:  # noqa: BLE001
            return None

    # ── Git commit after run ──────────────────────

    def _git_commit(self, task: str, result: LoopResult) -> None:
        """Auto-commit workspace changes after run."""
        if not self.auto_git_commit or self.workspace is None:
            return
        try:
            msg = f"agent: {result.finished_reason} | {task[:80]}"
            ok = git_commit(self.workspace, msg)
            if ok:
                self._trace({"type": "git_commit", "message": msg})
        except Exception as exc:                    # noqa: BLE001
            logger.warning("git commit failed: %s", exc)
            self._trace({"type": "git_commit_error", "error": str(exc)})


__all__ = ["AgentLoop", "LoopResult"]
