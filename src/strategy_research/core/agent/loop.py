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
from .context import ContextBuilder, estimate_tokens
from .progress import HeartbeatTimer
from .tools import ToolRegistry
from .trace import TraceWriter

logger = logging.getLogger(__name__)


# ── Compression thresholds (relative to threshold_tokens) ───────────

MICROCOMPACT_RATIO = 0.5    # at 50% of budget: trim large tool results
COLLAPSE_RATIO = 0.7        # at 70% of budget: summarize old messages (string)
LLM_SUMMARIZE_RATIO = 0.8   # at 80% of budget: LLM-structured summary
HARD_TRUNCATE_RATIO = 0.9   # at 90% of budget: keep only recent N
MICROCOMPACT_TOOL_RESULT_LIMIT = 500  # chars to keep per tool result in L1
COLLAPSE_KEEP_RECENT = 4            # keep last N messages verbatim in L2


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
        threshold_tokens: int = 8000,
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

        Returns an LLMResponse-like object with content and tool_calls.
        """
        from ..llm.parser import LLMResponse

        self._emit("thinking_start", {})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        try:
            for chunk in self.client.stream(messages):
                if chunk.delta_content:
                    if full_content == "" and chunk.delta_content:
                        # Transition: thinking_done before first text token
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {"text": chunk.delta_content})

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
        except Exception as exc:  # noqa: BLE001
            self._emit("thinking_end", {})
            raise

        self._emit("thinking_end", {})

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

        return parse_chat_response(raw_response)

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
        """Async version of _stream_chat using client.astream()."""
        self._emit("thinking_start", {})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        try:
            async for chunk in self.client.astream(messages):
                if chunk.delta_content:
                    if full_content == "":
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {"text": chunk.delta_content})

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
        except Exception as exc:  # noqa: BLE001
            self._emit("thinking_end", {})
            raise

        self._emit("thinking_end", {})

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

        return parse_chat_response(raw_response)

    def _build_hook_context(
        self, iteration: int, messages: list[dict[str, Any]],
    ) -> AgentHookContext:
        """Build AgentHookContext for the current iteration."""
        return AgentHookContext(iteration=iteration, messages=messages)

    # ── Shared logic (sync-safe: pure logic + trace + emit, no I/O) ──

    def _prepare_run(
        self, task: str, context: str | None,
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
        messages = self.context_builder.build_initial_messages(full_task)
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
        self._emit("iter_end", {
            "iteration": iteration,
            "finish_reason": "no_progress",
            "tool_calls_made": result.tool_calls_made,
        })
        return True

    def _handle_max_iter(self, result: LoopResult) -> None:
        """Populate max_iter result fields, emit trace + event."""
        result.finished_reason = "max_iter"
        result.answer = (
            f"Reached max_iterations={self.max_iterations} without a final answer."
        )
        self._trace({"type": "loop_end", "reason": "max_iter", "iteration": result.iterations})
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

    def run(self, task: str, *, context: str | None = None) -> LoopResult:
        """Run the loop until done.

        Args:
            task: User task description.
            context: Optional context to prepend to task (e.g., current_state).

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        full_task, result, messages, t0 = self._prepare_run(task, context)
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
                    response = self.client.chat(messages)
            except LLMError as exc:
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

    async def arun(self, task: str, *, context: str | None = None) -> LoopResult:
        """Async version of run() - all I/O points use await.

        Uses ``client.astream()`` / ``client.achat()`` for LLM calls,
        ``asyncio.to_thread()`` for tool execution, and ``asyncio.gather()``
        for parallel readonly tools.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        full_task, result, messages, t0 = self._prepare_run(task, context)
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
                    response = await self.client.achat(messages)
            except LLMError as exc:
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
            "arguments": tc.arguments,
            "call_id": tc.id,
            "iter": getattr(self, "_current_iter", 0),
        })

        # Inject workspace kwarg if not present
        kwargs = dict(tc.arguments)
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace

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
        status_str = "error" if is_error else "ok"
        output_preview = (output[:200] if isinstance(output, str) else str(output))[:200]
        self._emit("tool_result", {
            "tool": tc.name,
            "call_id": tc.id,
            "status": status_str,
            "ok": not is_error,  # backward compat
            "elapsed_ms": elapsed_ms,
            "preview": output_preview,
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
                "call_id": tc.id,
                "status": "error",
                "ok": False,
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

        self._emit("tool_call", {
            "tool": tc.name,
            "arguments": tc.arguments,
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
        status_str = "error" if is_error else "ok"
        output_preview = (output[:200] if isinstance(output, str) else str(output))[:200]
        self._emit("tool_result", {
            "tool": tc.name,
            "call_id": tc.id,
            "status": status_str,
            "ok": not is_error,
            "elapsed_ms": elapsed_ms,
            "preview": output_preview,
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

    # ── Context compression (3 layers) ─────────────

    def _maybe_compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply context compression if over threshold. Returns (compressed, applied_layers)."""
        tokens = estimate_tokens(messages)
        applied: list[str] = []

        if tokens < self.threshold_tokens * MICROCOMPACT_RATIO:
            return messages, applied

        # L1: Microcompact — trim large tool results
        if tokens >= self.threshold_tokens * MICROCOMPACT_RATIO:
            messages, l1_count = self._microcompact(messages)
            if l1_count:
                applied.append(f"microcompact({l1_count})")

        # Recompute after L1
        tokens = estimate_tokens(messages)

        # L2: Context collapse — summarize old messages, keep recent verbatim
        if tokens >= self.threshold_tokens * COLLAPSE_RATIO:
            old_len = len(messages)
            messages = self._context_collapse(messages)
            if len(messages) < old_len:
                applied.append(f"collapse({old_len}->{len(messages)})")

        # L2.5: Fix orphaned tool_call/tool_result pairs
        pre_fix_len = len(messages)
        messages = self._fix_tool_pairs(messages)
        if len(messages) < pre_fix_len:
            applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

        # L4: LLM-structured summary (between L2 and L3)
        tokens = estimate_tokens(messages)
        if tokens >= self.threshold_tokens * LLM_SUMMARIZE_RATIO:
            old_len = len(messages)
            summarized = self._llm_summarize(messages)
            if summarized is not None and len(summarized) < old_len:
                messages = summarized
                applied.append(f"llm_summarize({old_len}->{len(messages)})")

        # L3: Hard truncate — keep only recent N + system message
        tokens = estimate_tokens(messages)
        if tokens >= self.threshold_tokens * HARD_TRUNCATE_RATIO:
            old_len = len(messages)
            messages = self._hard_truncate(messages)
            if len(messages) < old_len:
                applied.append(f"truncate({old_len}->{len(messages)})")

        # L3.5: Fix again after truncation
        pre_fix_len = len(messages)
        messages = self._fix_tool_pairs(messages)
        if len(messages) < pre_fix_len:
            applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

        return messages, applied

    def _microcompact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """L1: Trim tool results > MICROCOMPACT_TOOL_RESULT_LIMIT chars."""
        count = 0
        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) <= MICROCOMPACT_TOOL_RESULT_LIMIT:
                continue
            truncated = content[:MICROCOMPACT_TOOL_RESULT_LIMIT] + "\n... [truncated]"
            messages[i] = dict(msg, content=truncated)
            count += 1
        return messages, count

    def _context_collapse(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """L2: Replace old messages with a summary; keep system + last N messages."""
        if len(messages) <= COLLAPSE_KEEP_RECENT + 1:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Keep system + recent verbatim
        recent = non_system[-COLLAPSE_KEEP_RECENT:]
        old = non_system[:-COLLAPSE_KEEP_RECENT]

        if not old:
            return messages

        # Summarize old messages as a single assistant message
        summary_parts = []
        for m in old:
            role = m.get("role", "?")
            content = m.get("content")
            if role == "user":
                summary_parts.append(f"[user] {(content or '')[:100]}")
            elif role == "assistant":
                if content:
                    summary_parts.append(f"[assistant] {content[:100]}")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    summary_parts.append(f"[tool_call] {fn.get('name', '?')}")
            elif role == "tool":
                tc_id = m.get("tool_call_id", "?")
                summary_parts.append(f"[tool_result:{tc_id}] {(content or '')[:80]}")

        summary = "[compressed summary]\n" + "\n".join(summary_parts)
        collapse_msg = {"role": "assistant", "content": summary}
        return system_msgs + [collapse_msg] + recent

    def _hard_truncate(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """L3: Keep only system + last COLLAPSE_KEEP_RECENT messages."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        return system_msgs + non_system[-COLLAPSE_KEEP_RECENT:]

    # ── Tool pair repair after compression ─────────

    def _fix_tool_pairs(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Repair orphaned tool_call / tool_result pairs after compression.

        After L2 collapse or L3 truncation, assistant messages with tool_calls
        may be removed while their tool results survive (or vice versa).
        This method:
          1. Collects all tool_call_ids from assistant messages
          2. Collects all tool_call_ids from tool result messages
          3. Removes orphaned tool results (no matching tool_call)
          4. Removes orphaned tool_calls from assistant messages (no matching result)
        """
        # Build sets of tool_call_ids from both sides
        assistant_call_ids: set[str] = set()
        result_ids: set[str] = set()

        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    tc_id = tc.get("id") if isinstance(tc, dict) else None
                    if tc_id:
                        assistant_call_ids.add(tc_id)
            elif msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id:
                    result_ids.add(tc_id)

        # Find orphans
        orphan_results = result_ids - assistant_call_ids
        orphan_calls = assistant_call_ids - result_ids

        if not orphan_results and not orphan_calls:
            return messages

        logger.debug(
            "fix_tool_pairs: removing %d orphan results, %d orphan calls",
            len(orphan_results), len(orphan_calls),
        )
        self._trace({
            "type": "fix_tool_pairs",
            "orphan_results": len(orphan_results),
            "orphan_calls": len(orphan_calls),
        })

        fixed: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")

            # Remove orphaned tool results
            if role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id in orphan_results:
                    continue
                fixed.append(msg)
                continue

            # Remove orphaned tool_calls from assistant messages
            if role == "assistant" and orphan_calls:
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    cleaned = []
                    for tc in tool_calls:
                        tc_id = tc.get("id") if isinstance(tc, dict) else None
                        if tc_id and tc_id not in orphan_calls:
                            cleaned.append(tc)
                    if cleaned:
                        msg = dict(msg, tool_calls=cleaned)
                    elif not msg.get("content"):
                        # assistant message with only orphaned tool_calls and no content: drop entirely
                        continue

            fixed.append(msg)

        return fixed

    # ── L4: LLM-based structured summary ──────────

    _LLM_SUMMARIZE_PROMPT = (
        "Summarize the following conversation into a concise structured note. "
        "Preserve: key decisions, tool results (metrics, data findings), "
        "file paths modified, and any open questions. "
        "Output 3-8 bullet points in plaintext. "
        "Do NOT include the system prompt or meta-instructions.\n\n"
        "{conversation}"
    )

    def _llm_summarize(
        self, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """L4: Use LLM to summarize old messages into a structured note.

        Returns compressed messages or None on failure.
        Keeps: system messages + last COLLAPSE_KEEP_RECENT messages.
        Replaces: everything in between with LLM-generated summary.
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= COLLAPSE_KEEP_RECENT:
            return None

        recent = non_system[-COLLAPSE_KEEP_RECENT:]
        old = non_system[:-COLLAPSE_KEEP_RECENT]

        if not old:
            return None

        # Build conversation text for summarization (truncated for token safety)
        parts: list[str] = []
        for m in old:
            role = m.get("role", "?")
            content = m.get("content", "")
            if role == "user":
                parts.append(f"User: {(content or '')[:300]}")
            elif role == "assistant":
                if content:
                    parts.append(f"Assistant: {content[:300]}")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    args_str = fn.get("arguments", "")[:100]
                    parts.append(f"ToolCall: {fn.get('name', '?')}({args_str})")
            elif role == "tool":
                tc_id = m.get("tool_call_id", "?")
                parts.append(f"ToolResult[{tc_id}]: {(content or '')[:200]}")

        conversation = "\n".join(parts)
        prompt = self._LLM_SUMMARIZE_PROMPT.format(conversation=conversation)

        try:
            summary_response = self.client.chat([
                {"role": "system", "content": "You are a concise conversation summarizer."},
                {"role": "user", "content": prompt},
            ])
            summary_text = summary_response.content or ""
            if not summary_text.strip():
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM summarize failed: %s", exc)
            return None

        summary_msg = {"role": "assistant", "content": f"[LLM summary]\n{summary_text}"}
        return system_msgs + [summary_msg] + recent

    # ── Async compression ─────────────────────────

    async def _allm_summarize(
        self, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Async version of _llm_summarize using client.achat()."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= COLLAPSE_KEEP_RECENT:
            return None

        recent = non_system[-COLLAPSE_KEEP_RECENT:]
        old = non_system[:-COLLAPSE_KEEP_RECENT]

        if not old:
            return None

        parts: list[str] = []
        for m in old:
            role = m.get("role", "?")
            content = m.get("content", "")
            if role == "user":
                parts.append(f"User: {(content or '')[:300]}")
            elif role == "assistant":
                if content:
                    parts.append(f"Assistant: {content[:300]}")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    args_str = fn.get("arguments", "")[:100]
                    parts.append(f"ToolCall: {fn.get('name', '?')}({args_str})")
            elif role == "tool":
                tc_id = m.get("tool_call_id", "?")
                parts.append(f"ToolResult[{tc_id}]: {(content or '')[:200]}")

        conversation = "\n".join(parts)
        prompt = self._LLM_SUMMARIZE_PROMPT.format(conversation=conversation)

        try:
            summary_response = await self.client.achat([
                {"role": "system", "content": "You are a concise conversation summarizer."},
                {"role": "user", "content": prompt},
            ])
            summary_text = summary_response.content or ""
            if not summary_text.strip():
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM summarize failed: %s", exc)
            return None

        summary_msg = {"role": "assistant", "content": f"[LLM summary]\n{summary_text}"}
        return system_msgs + [summary_msg] + recent

    async def _amaybe_compact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Async version of _maybe_compact - only L4 (LLM summarize) differs."""
        tokens = estimate_tokens(messages)
        applied: list[str] = []

        if tokens < self.threshold_tokens * MICROCOMPACT_RATIO:
            return messages, applied

        if tokens >= self.threshold_tokens * MICROCOMPACT_RATIO:
            messages, l1_count = self._microcompact(messages)
            if l1_count:
                applied.append(f"microcompact({l1_count})")

        tokens = estimate_tokens(messages)

        if tokens >= self.threshold_tokens * COLLAPSE_RATIO:
            old_len = len(messages)
            messages = self._context_collapse(messages)
            if len(messages) < old_len:
                applied.append(f"collapse({old_len}->{len(messages)})")

        pre_fix_len = len(messages)
        messages = self._fix_tool_pairs(messages)
        if len(messages) < pre_fix_len:
            applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

        tokens = estimate_tokens(messages)
        if tokens >= self.threshold_tokens * LLM_SUMMARIZE_RATIO:
            old_len = len(messages)
            summarized = await self._allm_summarize(messages)
            if summarized is not None and len(summarized) < old_len:
                messages = summarized
                applied.append(f"llm_summarize({old_len}->{len(messages)})")

        tokens = estimate_tokens(messages)
        if tokens >= self.threshold_tokens * HARD_TRUNCATE_RATIO:
            old_len = len(messages)
            messages = self._hard_truncate(messages)
            if len(messages) < old_len:
                applied.append(f"truncate({old_len}->{len(messages)})")

        pre_fix_len = len(messages)
        messages = self._fix_tool_pairs(messages)
        if len(messages) < pre_fix_len:
            applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

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
