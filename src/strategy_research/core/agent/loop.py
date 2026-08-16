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
import inspect
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..git import git_commit
from ..hooks.composite import CompositeHook
from ..hooks.context import AgentHookContext
from ..llm import LLMConfig, LLMResponse, OpenAICompatClient, ToolCall
from ..llm.errors import LLMError
from ..memory.persistent import PersistentMemory
from .circuit_breaker import RetryPolicy, ToolLoopCircuitBreaker
from .compact import CompactConfig, compact_messages
from .context import ContextBuilder, estimate_tokens
from .progress import HeartbeatTimer
from .tools import TRANSIENT_TOOL_ERRORS, ToolContext, ToolRegistry
from .trace import TraceWriter

logger = logging.getLogger(__name__)


def _run_coro_in_sync(coro: Any) -> Any:
    """Run a coroutine to completion from synchronous code.

    Works both outside AND inside a running event loop (``AgentLoop.run``
    is invoked from async contexts by ``role_factory``): when a loop is
    already running, the coroutine executes on a background thread with
    its own loop and the caller blocks until it finishes.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        result["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return result["value"]


# ── Tool-level auto-retry (transient errors only) ───────────────────
# Shared by sync (_execute_tool_call) and async (_aexecute_tool_call).
# Transient error types live in tools.py (single source, used by
# BaseTool.invoke to re-raise them to this retry loop).
_TOOL_MAX_RETRIES = 2
_TOOL_RETRY_DELAY = 2.0

# SSE tool_result 的 result 字段大小上限（字符）。完整 result 供前端解析
# （run_backtest metrics → 右侧面板）；超大输出（如 read_file 大文件）截断
# 防撑爆 SSE/DB。preview 字段始终为 200 字符截断（兼容旧消费方）。
_TOOL_RESULT_MAX = 50_000

# Tool result 入 LLM history 前的统一截断上限（字符）。
# 超长输出在中间截断，保留头尾并标记 [truncated]。
# 环境变量 SR_TOOL_RESULT_MAX_CHARS 可覆盖（0 = 不截断）。
_TOOL_RESULT_HISTORY_MAX = int(os.environ.get("SR_TOOL_RESULT_MAX_CHARS", "30000"))


# ── Cached GoalStore (goal-snapshot injection) ─────────────────────
# Constructing a GoalStore per loop iteration leaked one SQLite
# connection each (the store had no close() before F1-2); a single
# long-lived store per db path avoids the churn while keeping test
# isolation when QUANTNODES_RESEARCH_GOAL_DB_PATH is overridden.
_goal_store_cache: dict[Path, Any] = {}


def _get_goal_store() -> Any:
    """Return the cached GoalStore for the default goal DB path."""
    from ..goal.store import GoalStore
    store = GoalStore()
    key = store.db_path
    cached = _goal_store_cache.get(key)
    if cached is None:
        _goal_store_cache[key] = store
        return store
    store.close()
    return cached


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


@contextmanager
def compaction_persister_registered(fn: Any):
    """Context manager for safe persister registration in tests.

    Registers ``fn`` as the compaction persister for the duration of
    the ``with`` block, then restores the previous value (typically
    ``None``) on exit — even if the block raises.

    Production code should call ``register_compaction_persister`` once
    at process start; this context manager is for tests that need to
    inject a mock without leaking state across tests.

    Example::

        with compaction_persister_registered(mock_persist):
            loop._persist_compaction_event("summary", "recent")
    """
    global _compaction_persister
    previous = _compaction_persister
    _compaction_persister = fn
    try:
        yield
    finally:
        _compaction_persister = previous


# ── L7: LoopStrategy context helper ────────────────────────────────────


def _make_strategy_ctx(
    loop: Any,
    messages: list[dict[str, Any]],
    response: Any,
    result: Any,
    iteration: int,
    hook_ctx: Any = None,
) -> Any:
    """Build a transient ``LoopContext`` for consulting Stop /
    Continuation / Progress / Resilience steps without disturbing the
    legacy hard-coded control flow. v0.2 keeps the strategy steps
    read-only — the legacy path still drives everything; a custom
    step can only flip ``ctx.should_stop`` (the loop body honours it).

    ``hook_ctx`` is carried through so a step that wants AgentHookContext
    access (e.g. to read usage) gets the same instance the hook system
    uses — zero extra synchronisation.
    """
    from .strategy.loop_context import LoopContext

    return LoopContext(
        task="",
        messages=list(messages),
        iteration=iteration,
        response=response,
        response_was_tool_call=bool(getattr(response, "tool_calls", None)),
        response_content=getattr(response, "content", "") or "",
        result=result,
        hook_ctx=hook_ctx,
    )


def _inject_agent_loop(strategy: Any, agent_loop: Any) -> None:
    """L7 — bind ``agent_loop`` to every step on ``strategy`` that opts in.

    Steps that need the loop (e.g. DefaultPreRunStep, DefaultLLMCallStep)
    define a ``bind_agent_loop`` method; custom or no-op steps simply
    ignore the call. Walks the 9 known step slots.
    """
    slots = (
        "pre_run", "llm_call", "compaction", "stop", "continuation",
        "progress", "resilience", "tool_execution", "finalization",
    )
    for slot in slots:
        step = getattr(strategy, slot, None)
        if step is None:
            continue
        bind = getattr(step, "bind_agent_loop", None)
        if callable(bind):
            bind(agent_loop)


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
        # ── v2 path parameterization (study scenario) ────────────────
        strategy_dir: Path | None = None,
        runs_dir: Path | None = None,
        results_tsv: Path | None = None,
        write_roots: tuple[str, ...] | None = None,
        read_roots: tuple[str, ...] | None = None,
        enable_goal_injection: bool = True,
        enable_hypothesis_auto_create: bool = True,
        hooks: CompositeHook | None = None,
        session_manager: Any | None = None,
        on_event: Any | None = None,
        stream_mode: bool = True,
        compact_config: CompactConfig | None = None,
        event_bus: Any | None = None,
        circuit_breaker: ToolLoopCircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
        enable_claim_validation: bool = False,
        strict_claim_validation: bool = False,
        # ── P1-5: LoopStrategy integration ──────────────────────
        # v0.1: accept the strategy spec; build / resolve into a
        # ``LoopStrategy`` and store on self. The actual rewrite of
        # ``_run_loop_core`` to drive the strategy lands in L7.
        # Accepts: ``LoopStrategy`` / str / dict / None.
        strategy: Any | None = None,
        # ── DSH-inspired: pluggable context injectors ──────────
        injectors: list[Any] | None = None,
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
        self.strategy_dir = strategy_dir
        self.runs_dir = runs_dir
        self.results_tsv = results_tsv
        self.write_roots = write_roots
        self.read_roots = read_roots
        self.enable_goal_injection = enable_goal_injection
        self.enable_hypothesis_auto_create = enable_hypothesis_auto_create
        self._hooks = hooks
        self._session_manager = session_manager
        self._on_event = on_event
        self._stream_mode = stream_mode
        self._event_bus = event_bus
        self._circuit_breaker = circuit_breaker
        self._retry_policy = retry_policy or RetryPolicy()
        # P1-5: build / resolve the LoopStrategy from the spec.
        # v0.1 stores it on self; ``_run_loop_core`` does not yet
        # consult it (that's L7's job).
        from .strategy.profile_resolver import resolve_loop_strategy
        self._strategy = resolve_loop_strategy(strategy)
        # L7 v0.2: when the caller passed an explicit strategy, its
        # config drives the loop (max_iterations etc.); otherwise we
        # fall back to the constructor kwargs. This keeps existing
        # ``AgentLoop(max_iterations=2)`` tests working while letting
        # an explicit profile/strategy override the cap.
        self._strategy_explicit = strategy is not None
        # L7: inject self into every Default*Step so steps that opt
        # in (currently DefaultPreRunStep + DefaultLLMCallStep) can
        # call AgentLoop methods. Custom steps ignore the binding.
        _inject_agent_loop(self._strategy, self)
        self._enable_claim_validation = enable_claim_validation
        self._strict_claim_validation = strict_claim_validation
        self.cc = compact_config or config.compact_config or CompactConfig()
        self._previous_summary: str | None = None
        # DSH-inspired: pluggable context injectors (sorted by order).
        if injectors is not None:
            self._injectors = sorted(injectors, key=lambda i: getattr(i, "order", 0))
        else:
            from .context_injector import build_default_injectors
            self._injectors = build_default_injectors()

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
        # Subagent delegation counter (reset per arun())
        self._subagent_count: list[int] = [0]  # mutable ref for SubAgentTool
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

    def _trace_and_emit(self, event_type: str, data: dict | None = None) -> None:
        """Write a trace entry AND emit it onto the event bus.

        Keeps ``trace.jsonl`` (backward-compat) and the event_log in sync
        for the same logical event, so the Trajectory View can be derived
        from the event_log alone (single source of truth).
        """
        self._trace({"type": event_type, **(data or {})})
        self._emit(event_type, data or {})

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
        # thinking_start BEFORE text.started: both the frontend parts
        # array and the projector persist parts in event order, so the
        # thinking block must be created first to render above the text
        # body (both live and after refresh).
        self._emit("thinking_start", {})
        self._emit("text.started", {"text_id": text_id})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self.registry.get_definitions() or None
        try:
            for chunk in self.client.stream(messages, tools=tools):
                # Thinking tokens (extracted by provider adapter). The raw
                # pre-cleanup text travels alongside so event_log keeps the
                # model's original output (raw) while consumers read the
                # cleaned delta.
                if chunk.delta_thinking:
                    self._emit("thinking_delta", {
                        "delta": chunk.delta_thinking,
                        "raw": chunk.raw_thinking,
                    })

                if chunk.delta_content:
                    if full_content == "" and chunk.delta_content:
                        # Transition: thinking_done before first text token
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                        "raw": chunk.raw_content,
                    })

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        self._accumulate_tool_call(accumulated_tool_calls, tc_delta)

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

        return self.client.parse_response(raw_response)

    @staticmethod
    def _accumulate_tool_call(
        accumulated: list[dict[str, Any]], tc_delta: dict[str, Any]
    ) -> None:
        """Merge a streaming tool-call delta into the accumulated list."""
        idx = tc_delta.get("index", 0)
        while len(accumulated) <= idx:
            accumulated.append({
                "id": tc_delta.get("id", ""),
                "type": "function",
                "function": {"name": "", "arguments": ""},
            })
        tc = accumulated[idx]
        if tc_delta.get("id"):
            tc["id"] = tc_delta["id"]
        if tc_delta.get("type"):
            tc["type"] = tc_delta["type"]
        func_delta = tc_delta.get("function", {})
        if func_delta.get("name"):
            tc["function"]["name"] = func_delta["name"]
        if func_delta.get("arguments"):
            tc["function"]["arguments"] += func_delta["arguments"]

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
        # thinking_start BEFORE text.started: both the frontend parts
        # array and the projector persist parts in event order, so the
        # thinking block must be created first to render above the text
        # body (both live and after refresh).
        self._emit("thinking_start", {})
        self._emit("text.started", {"text_id": text_id})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self.registry.get_definitions() or None
        try:
            chunk_count = 0
            async for chunk in self.client.astream(messages, tools=tools):
                chunk_count += 1
                if chunk_count <= 3:
                    logger.debug(
                        "[DIAG] _astream_chat chunk#%d: delta_content=%.100r "
                        "delta_thinking=%.100r finish_reason=%r tool_calls=%d usage=%r",
                        chunk_count,
                        chunk.delta_content[:100] if chunk.delta_content else "",
                        chunk.delta_thinking[:100] if chunk.delta_thinking else "",
                        chunk.finish_reason,
                        len(chunk.delta_tool_calls),
                        chunk.usage,
                    )
                if chunk.delta_thinking:
                    self._emit("thinking_delta", {
                        "delta": chunk.delta_thinking,
                        "raw": chunk.raw_thinking,
                    })
                    # 让出 event loop，让前端逐字看到 thinking
                    await asyncio.sleep(0)

                if chunk.delta_content:
                    if full_content == "":
                        self._emit("thinking_done", {})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                        "raw": chunk.raw_content,
                    })
                    # 强制让出 event loop，让 SSE _event_generator 有机会
                    # 逐个 yield text_delta（避免 async for 连续处理多个
                    # chunk 导致 _event_generator 批量 yield → "一段一段"）
                    await asyncio.sleep(0)

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        self._accumulate_tool_call(accumulated_tool_calls, tc_delta)

                if chunk.usage:
                    usage = chunk.usage

                if chunk.finish_reason:
                    break
        except Exception:  # noqa: BLE001
            self._emit("thinking_end", {})
            self._emit("text.ended", {"text_id": text_id, "text": full_content})
            raise

        self._emit("thinking_end", {})
        self._emit("text.ended", {"text_id": text_id, "text": full_content})
        # llm_usage 只在 LLM call 结束时 emit 一次（而非每 chunk）。
        # 每 chunk emit 会导致 event_callback 再 emit session_total_tokens，
        # 3 倍事件洪流淹没 text_delta，且 input_tokens 会被错误累加。
        if usage:
            self._emit("llm_usage", usage)

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

        logger.debug(
            "[DIAG] _astream_chat final: full_content=%.200r tool_calls=%d usage=%r",
            full_content[:200] if full_content else "",
            len(accumulated_tool_calls),
            usage,
        )

        return self.client.parse_response(raw_response)

    def _build_hook_context(
        self, iteration: int, messages: list[dict[str, Any]],
    ) -> AgentHookContext:
        """Build AgentHookContext for the current iteration."""
        return AgentHookContext(iteration=iteration, messages=messages)

    # ── P0-2 D: capability seam builders ────────────────────────

    def _build_data_store(self):
        """Return the default DataStore for this AgentLoop instance.

        v0.1 always returns ``get_store("duckdb")`` — the registry's
        default provider. Tests / callers can override by setting
        ``self._data_store_override`` before ainvoke runs.
        """
        override = getattr(self, "_data_store_override", None)
        if override is not None:
            return override
        from ..storage import get_store
        return get_store()

    def _build_sandbox(self):
        """Return the default ExecutionSandbox for this AgentLoop instance.

        v0.1 always returns a fresh ``StaticSandbox`` rooted at
        ``self.workspace``. Overridable via
        ``self._sandbox_override``.
        """
        override = getattr(self, "_sandbox_override", None)
        if override is not None:
            return override
        from .sandbox import StaticSandbox
        workspace = self.workspace or getattr(self, "_fallback_workspace", None)
        if workspace is None:
            # StaticSandbox needs a workspace; in tests where workspace
            # isn't set we still return a sandbox bound to cwd. Tools
            # that call resolve_write/read without a real workspace
            # already raise PathValidationError, so this is safe.
            from pathlib import Path
            workspace = Path.cwd()
        return StaticSandbox(workspace)

    # ── P1-5: LoopStrategy accessor ──────────────────────────────

    def get_strategy(self):
        """Return the ``LoopStrategy`` resolved from the ``strategy=``
        constructor arg (or the default ReAct strategy when none was
        supplied). v0.1 stores the strategy on ``self._strategy`` but
        ``_run_loop_core`` does not yet consult it (L7 work)."""
        return self._strategy

    # ── L7 v0.2: step execution with uniform error isolation ──────

    async def _call_step(self, step: Any, ctx: Any, *, async_mode: bool) -> Any:
        """Execute a strategy Step with try/except isolation.

        A failing Step must not crash the agent loop: we log the error,
        set ``ctx.should_stop`` so the caller can break safely, and
        return the (possibly partially-mutated) context. Sync steps
        (evaluate / is_no_progress / is_open) are handled transparently
        via ``inspect.isawaitable``.
        """
        try:
            result = step.execute(ctx, async_mode=async_mode)
            if inspect.isawaitable(result):
                return await result
            return result
        except Exception as exc:  # noqa: BLE001 — isolated per-step
            logger.warning("step %s failed: %s", step.name, exc)
            ctx.should_stop = True
            ctx.stop_reason = f"step_{step.name}"
            return ctx

    # ── Shared logic (sync-safe: pure logic + trace + emit, no I/O) ──

    def _prepare_run(
        self, task: str, context: str | None,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[str, LoopResult, list[dict[str, Any]], float]:
        """Assemble full_task, init result/messages, emit loop_start trace."""
        self._maybe_auto_create_hypothesis(task)
        full_task = task
        if context:
            full_task = context + "\n\n" + task
        # DSH-inspired: run pre-run injectors (order=-100, e.g. GoalContextInjector)
        for injector in self._injectors:
            if getattr(injector, "order", 0) < 0:
                try:
                    full_task = injector.inject_pre_run(self, full_task, [])
                except Exception:  # noqa: BLE001
                    pass  # injectors must not break the loop
        result = LoopResult()
        messages = self.context_builder.build_initial_messages(full_task, history=history)
        result.messages = list(messages)
        t0 = time.perf_counter()
        self._trace_and_emit("loop_start", {
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
        self._trace_and_emit("compression", {"applied": applied, "iteration": iteration})
        for layer in applied:
            self._emit("compact", {
                "layer": layer,
                "iteration": iteration,
                "summary": f"Context compression: {layer}",
            })

    def _emit_iter_start(self, iteration: int, messages: list[dict[str, Any]]) -> None:
        """Emit iter_start trace + event, set _current_iter."""
        self._trace_and_emit("iter_start", {
            "iteration": iteration,
            "tokens": estimate_tokens(messages),
            "max_iterations": self.max_iterations,
        })
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
        self._trace_and_emit("llm_response", {
            "iteration": iteration,
            "finish_reason": response.finish_reason,
            "has_tool_calls": response.has_tool_calls(),
            "tool_call_count": len(response.tool_calls),
            "content": response.content or "",
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

    def _breaker_open_messages(
        self, tool_calls: list[ToolCall],
    ) -> list[dict[str, Any]]:
        """Return tool error messages when circuit breaker is OPEN.

        Tells the LLM to try a different approach instead of repeating
        the same failing tool calls.
        """
        state = self._circuit_breaker.to_dict() if self._circuit_breaker else {}
        return [
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({
                    "status": "error",
                    "error": "circuit_breaker_open",
                    "message": (
                        f"Tool '{tc.name}' is temporarily unavailable "
                        "(circuit breaker open). Please try a different "
                        "approach or tool."
                    ),
                    "circuit_state": state,
                }, ensure_ascii=False),
            }
            for tc in tool_calls
        ]

    def _check_no_progress(
        self, tool_hashes: list[str], response: LLMResponse,
        result: LoopResult, iteration: int,
        *,
        hashes_pre_recorded: bool = False,
    ) -> bool:
        """Update _recent_hashes, detect no_progress. If triggered, fill result + emit. Return True if triggered.

        L7 v0.2: when ``hashes_pre_recorded=True`` (the strategy's
        ProgressStep already recorded the hashes into ``_recent_hashes``),
        skip the ``extend`` so the window isn't double-counted. The
        side-effects (record_event / circuit_breaker / emit) still run
        here.
        """
        if not hashes_pre_recorded:
            self._recent_hashes.extend(tool_hashes)
            if len(self._recent_hashes) > self.no_progress_window:
                self._recent_hashes = self._recent_hashes[-self.no_progress_window:]
        if not self._detect_no_progress():
            return False
        from ..study.hanging_events import record_event
        record_event(
            "no_progress",
            session_id=getattr(self, "session_id", None),
            detail=(
                f"iteration={iteration} "
                f"window={self.no_progress_window} "
                f"tool_calls={len(self._recent_hashes)}"
            ),
        )
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_no_progress()
        result.finished_reason = "no_progress"
        result.answer = (
            response.content or
            f"No progress detected (last {self.no_progress_window} tool calls identical)"
        )
        self._trace_and_emit("loop_end", {"reason": "no_progress", "iteration": iteration})
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
        self._trace_and_emit("loop_end", {"reason": "max_iter", "iteration": result.iterations})
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
        self._trace_and_emit("loop_end", {"reason": "stop", "iteration": iteration})
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
        self._trace_and_emit("loop_final", {
            "reason": result.finished_reason,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
            "elapsed_s": round(elapsed, 2),
            "compression": result.compression_applied,
        })

    def _run_claim_validation(
        self, result: LoopResult, messages: list[dict[str, Any]],
    ) -> None:
        """Validate metric claims in the final answer against tool results.

        Attaches ``ClaimValidationResult.__dict__`` to
        ``result.metrics["claim_validation"]`` so the API layer can
        surface it in assistant-message metadata (→ 🟡/🔴 badge).

        When ``strict_claim_validation`` is enabled and unverified
        claims exist, a soft warning is appended to the answer (never
        rewrites or deletes the model's text).
        """
        if not self._enable_claim_validation:
            return
        from .validators import validate_claims

        tool_texts = [
            m.get("content", "")
            for m in messages if m.get("role") == "tool"
        ]
        cv = validate_claims(result.answer or "", tool_texts)
        result.metrics["claim_validation"] = cv.__dict__
        if self._strict_claim_validation and not cv.ok:
            warning = (
                "\n\n> ⚠️ 数据真实性警告：以下数字未在工具返回值中找到，"
                f"可能为模型推测：{', '.join(cv.unverified)}"
            )
            result.answer = (result.answer or "") + warning
            self._trace({
                "type": "claim_validation_warning",
                "unverified": cv.unverified,
            })

    # ── Public API ───────────────────────────────

    def run(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Run the loop until done (sync path).

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

        return _run_coro_in_sync(
            self._run_loop_core(task, context, history, async_mode=False)
        )

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
        return await self._run_loop_core(task, context, history, async_mode=True)

    async def _run_loop_core(  # noqa: C901
        self,
        task: str,
        context: str | None,
        history: list[dict[str, Any]] | None,
        *,
        async_mode: bool,
    ) -> LoopResult:
        """Shared run-loop core (single source for ``run``/``arun``).

        ``async_mode`` selects the await-based I/O (achat/astream/ainvoke/
        gather/afire_hooks) vs the synchronous equivalents (chat/stream/
        invoke/ThreadPoolExecutor/fire_hooks). Both paths share the same
        iteration/hook/compact/stop/no-progress semantics.
        """
        from ..observability.trace import _session_id, _trace_id

        if not _trace_id.get():
            _trace_id.set(uuid.uuid4().hex[:12])
        if self.session_id and not _session_id.get():
            _session_id.set(self.session_id)
        full_task, result, messages, t0 = self._prepare_run(task, context, history)
        response = None  # L7 v0.4: set by LLMCallStep each iteration
        self._subagent_count[0] = 0  # reset per-turn delegation counter
        hook_ctx = self._build_hook_context(0, messages)

        async def _fire(name: str, ctx: Any, *args: Any) -> None:
            # Both modes await hook coroutines on the current loop: the
            # sync adapter (_fire_hooks) runs them on a throwaway loop,
            # which raises in 3.10 when the coroutine was created inside
            # a running loop (the sync `run` bridge thread).
            await self._afire_hooks(name, ctx, *args)

        await _fire("before_run", hook_ctx)

        # L7 v0.2: iteration cap — an explicit strategy config drives
        # it (a profile said max_iterations=N); otherwise fall back to
        # the constructor ``self.max_iterations`` so existing tests /
        # callers that pass max_iterations=2 keep working unchanged.
        max_iter = getattr(
            self._strategy.config, "max_iterations", self.max_iterations,
        ) if self._strategy_explicit else self.max_iterations
        for iteration in range(1, max_iter + 1):
            result.iterations = iteration
            hook_ctx = self._build_hook_context(iteration, messages)

            # L7 v0.5: compaction + per-iteration observability delegated
            # to the strategy's CompactionStep. The step runs the
            # compaction engine, emits _emit_compaction, _emit_iter_start.
            comp_ctx = _make_strategy_ctx(
                self, messages, None, result, iteration, hook_ctx,
            )
            comp_ctx = await self._call_step(
                self._strategy.compaction, comp_ctx, async_mode=async_mode,
            )
            messages = comp_ctx.messages

            # DSH-inspired: run per-iteration injectors (order=0, e.g. TodosInjector)
            for injector in self._injectors:
                if getattr(injector, "order", 0) == 0:
                    try:
                        injector.inject_per_iteration(self, messages)
                    except Exception:  # noqa: BLE001
                        pass  # injectors must not break the loop

            # L7 v0.4: LLM call + before_iteration + on_error delegated
            # to the strategy's LLMCallStep. The step fires
            # before_iteration before _get_response and on_error after
            # _handle_llm_error.
            llm_ctx = _make_strategy_ctx(
                self, messages, None, result, iteration, hook_ctx,
            )
            llm_ctx = await self._call_step(
                self._strategy.llm_call, llm_ctx, async_mode=async_mode,
            )
            if llm_ctx.should_stop:
                break
            response = llm_ctx.response
            if response is None:
                break
            messages = llm_ctx.messages

            if not response.has_tool_calls():
                # L7: consult the strategy's ContinuationStep before
                # the goal check. DefaultContinuationStep is a no-op
                # (returns False); the legacy path still runs.
                should_continue, _ = self._strategy.continuation.evaluate(
                    _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
                )
                if should_continue:
                    continue
                # DSH-inspired: run post-response injectors (order>=100, e.g. GoalContinuationInjector)
                injector_continued = False
                for injector in self._injectors:
                    if getattr(injector, "order", 0) >= 100:
                        try:
                            if injector.inject_post_response(self, response, messages, result, iteration):
                                injector_continued = True
                                break
                        except Exception:  # noqa: BLE001
                            pass  # injectors must not break the loop
                if injector_continued:
                    continue
                # L7: consult the strategy's StopStep before the legacy
                # _handle_stop. DefaultStopStep is a no-op; the legacy
                # path still runs.
                should_stop, _stop_reason = self._strategy.stop.evaluate(
                    _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
                )
                if should_stop:
                    await _fire("after_iteration", hook_ctx)
                    break
                self._handle_stop(response, result, iteration)
                await _fire("after_iteration", hook_ctx)
                break

            # L7 v0.2 decision point 3: circuit-breaker gate delegated to
            # the strategy's ResilienceStep. DefaultResilienceStep reads
            # ``self._circuit_breaker`` directly, so behaviour is
            # identical unless a custom strategy overrides is_open().
            if self._strategy.resilience.is_open(
                _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
            ):
                tool_result_msgs = self._breaker_open_messages(response.tool_calls)
                self._append_tool_results(response.tool_calls, tool_result_msgs, messages, result)
                await _fire("after_iteration", hook_ctx)
                continue

            # L7 v0.3: tool execution + tool lifecycle hooks delegated
            # to the strategy's ToolExecutionStep. The step fires
            # before_execute_tools / on_tool_error / after_tool_executed
            # and collects hashes into ctx.metadata["tool_hashes"].
            tool_ctx = _make_strategy_ctx(
                self, messages, response, result, iteration, hook_ctx
            )
            tool_ctx = await self._call_step(
                self._strategy.tool_execution, tool_ctx, async_mode=async_mode,
            )
            if tool_ctx.should_stop:
                await _fire("after_iteration", hook_ctx)
                break
            tool_result_msgs = tool_ctx.metadata.get("tool_result_msgs") or []
            tool_hashes = tool_ctx.metadata.get("tool_hashes") or []
            # The step already appended tool results into ctx.messages
            # via _append_tool_results; messages = tool_ctx.messages to
            # pick them up.
            messages = tool_ctx.messages
            await _fire("after_iteration", hook_ctx)

            # L7 v0.2 decision point 2: no-progress detection delegated
            # to the strategy's ProgressStep. We record the hashes into
            # the step's window, then ask it whether the window shows
            # no progress. DefaultProgressStep mirrors AgentLoop's
            # legacy _recent_hashes / _detect_no_progress, so behaviour
            # is identical unless a custom strategy overrides it.
            progress_ctx = _make_strategy_ctx(
                self, messages, response, result, iteration, hook_ctx
            )
            for h in tool_hashes:
                self._strategy.progress.record_hash(progress_ctx, h)
            if self._strategy.progress.is_no_progress(progress_ctx):
                # Keep the legacy side-effect path (record_event,
                # circuit_breaker.record_no_progress, emit) intact.
                # hashes_pre_recorded=True avoids double-counting the
                # window (record_hash already did the extend above).
                self._check_no_progress(
                    tool_hashes, response, result, iteration,
                    hashes_pre_recorded=True,
                )
                await _fire("after_run", hook_ctx, result)
                return result
        else:
            self._handle_max_iter(result)

        # L7 v0.4: finalization (metrics + claim validation) + the
        # normal-end ``after_run`` hook delegated to the strategy's
        # FinalizationStep. The no-progress early-return path fires
        # after_run in the skeleton above (it returns before reaching
        # this Step) — that call is intentionally kept.
        fin_ctx = _make_strategy_ctx(
            self, messages, response, result, result.iterations, hook_ctx,
        )
        fin_ctx.t0 = t0
        fin_ctx = await self._call_step(
            self._strategy.finalization, fin_ctx, async_mode=async_mode,
        )
        if async_mode:
            await asyncio.to_thread(self._git_commit, full_task, result)
        else:
            self._git_commit(full_task, result)

        if self._trace_writer is not None:
            result.trace_path = str(self._trace_writer.path)

        return result

    async def _get_response(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        async_mode: bool,
        hook_ctx: Any,
        result: LoopResult,
    ) -> "LLMResponse | None":
        """Chat/stream call with the non-streaming fallback.

        Returns the response, or ``None`` when the stream-fallback error
        path already fired ``on_error`` (caller breaks the loop).
        Raises ``LLMError`` when the error was NOT stream-fallback-eligible
        (caller handles + fires ``on_error``).
        """
        try:
            if self._stream_mode:
                # Trace LLM request envelope before streaming
                self._trace_llm_request(messages, iteration, tools=None)
                if async_mode:
                    return await self._astream_chat(messages, iteration)
                return self._stream_chat(messages, iteration)
            tools = self.registry.get_definitions() or None
            # Trace LLM request envelope before non-streaming call
            self._trace_llm_request(messages, iteration, tools=tools)
            if async_mode:
                return await self.client.achat(messages, tools=tools)
            return self.client.chat(messages, tools=tools)
        except LLMError as exc:
            if not (self._stream_mode and not self._is_stream_required_error(exc)):
                raise
            # Streaming failed for non-streaming-required reasons
            # (e.g. provider doesn't support SSE, parsing error on a
            # partial chunk). Fall back to non-streaming chat().
            try:
                tools = self.registry.get_definitions() or None
                # Trace fallback LLM request
                self._trace_llm_request(messages, iteration, tools=tools)
                if async_mode:
                    return await self.client.achat(messages, tools=tools)
                return self.client.chat(messages, tools=tools)
            except LLMError as exc2:
                self._handle_llm_error(exc2, iteration, result)
                await self._afire_hooks("on_error", hook_ctx, exc2)
                return None

    @staticmethod
    async def _fire_tool_result_hooks(_fire, hook_ctx, tool_calls, tool_result_msgs) -> None:
        """Fire per-tool-result hooks (on_tool_error / after_tool_executed)."""
        for tc, tool_result_msg in zip(tool_calls, tool_result_msgs):
            if tool_result_msg.get("content", "").startswith('{"status": "error"'):
                await _fire("on_tool_error", hook_ctx, tc, RuntimeError(tool_result_msg["content"]))
            else:
                await _fire("after_tool_executed", hook_ctx, tc, tool_result_msg)

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

    async def _execute_tool_call_core(
        self, tc: ToolCall, result: LoopResult, *, async_mode: bool
    ) -> dict[str, Any]:
        """Shared tool-call core (single source for both modes).

        ``async_mode`` selects ``tool.ainvoke`` (permission-gated, awaited
        on the loop) vs ``tool.invoke`` (direct sync call) and
        ``asyncio.sleep`` vs ``time.sleep`` for the transient-retry delay.
        The sync entry runs this core via ``_run_coro_in_sync`` (from a
        pool thread there is no running loop, so no nested thread).
        """
        result.tool_calls_made += 1
        tool = self.registry.get(tc.name)
        if tool is None:
            logger.warning("tool '%s' not in registry", tc.name)
            self._trace_and_emit("tool_error", {"tool": tc.name, "error": "not in registry"})
            self._emit("tool_result", {
                "tool": tc.name,
                "id": tc.id,
                "call_id": tc.id,
                "status": "error",
                "ok": False,  # backward compat
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

        # Inject workspace + session_id kwargs if not present
        kwargs = dict(tc.arguments)
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace
        if "session_id" not in kwargs and self.session_id is not None:
            kwargs["session_id"] = self.session_id

        # Inject progress callback so tools can report progress steps
        def _progress_callback(steps: list[str]) -> None:
            self._emit("tool_progress", {
                "id": tc.id,
                "steps": steps,
            })
        kwargs["_progress_callback"] = _progress_callback

        # v2: explicit ToolContext (workspace/session_id kwargs stay for
        # legacy tools until P3 migration removes them). The
        # permission_evaluator / permission_gateway / tool_call_id
        # fields wire the Tier 1 A1 permission gate into ainvoke().
        # P0-2 D: data_store + sandbox are auto-injected as the default
        # capability seams. Tools consume them via tools_capability
        # helpers; existing tools that don't use them are unaffected.
        kwargs["ctx"] = ToolContext(
            workspace=self.workspace,
            session_id=self.session_id,
            strategy_dir=self.strategy_dir,
            runs_dir=self.runs_dir,
            results_tsv=self.results_tsv,
            write_roots=self.write_roots,
            read_roots=self.read_roots,
            emit_progress=_progress_callback,
            emit_event=self._emit,
            message_id=getattr(self, "_current_message_id", None),
            permission_evaluator=getattr(self, "_permission_evaluator", None),
            permission_gateway=getattr(self, "_permission_gateway", None),
            tool_call_id=tc.id,
            data_store=self._build_data_store(),
            sandbox=self._build_sandbox(),
        )

        # SubAgentTool injection: emit_event, message_id, count ref, parent registry
        if tc.name == "delegate_to_agent":
            kwargs["emit_event"] = self._emit
            kwargs["message_id"] = getattr(self, "_current_message_id", None)
            kwargs["_subagent_count_ref"] = self._subagent_count
            kwargs["_parent_registry"] = self.registry

        # TodoWriteTool injection: emit_event (session_id already injected)
        if tc.name == "todo_write":
            kwargs["emit_event"] = self._emit

        t0 = time.perf_counter()
        # ── Tool-level auto-retry for transient errors ──────────────
        last_exc = None
        for _attempt in range(_TOOL_MAX_RETRIES):
            try:
                if async_mode:
                    output = await tool.ainvoke(kwargs, kwargs.get("ctx"))
                else:
                    output = tool.invoke(kwargs)
                last_exc = None
                break
            except TRANSIENT_TOOL_ERRORS as exc:
                last_exc = exc
                logger.warning("tool %s raised %s (attempt %d/%d): %s",
                               tc.name, type(exc).__name__, _attempt + 1,
                               _TOOL_MAX_RETRIES, exc)
                if _attempt < _TOOL_MAX_RETRIES - 1:
                    if async_mode:
                        await asyncio.sleep(_TOOL_RETRY_DELAY)
                    else:
                        time.sleep(_TOOL_RETRY_DELAY)
            except Exception as exc:                    # noqa: BLE001
                logger.exception("tool %s raised", tc.name)
                output = json.dumps(
                    {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
                last_exc = None
                break
        else:
            # All retries exhausted for transient errors
            output = json.dumps(
                {"status": "error", "error": f"{type(last_exc).__name__}: {last_exc}",
                 "hint": "tool failed after retries; check input parameters or data quality"},
                ensure_ascii=False,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Emit tool_result event
        is_error = isinstance(output, str) and output.startswith('{"status": "error"')
        status_str = "error" if is_error else "done"

        # Update circuit breaker
        if self._circuit_breaker is not None:
            if is_error:
                self._circuit_breaker.record_failure(tc.name)
            else:
                self._circuit_breaker.record_success(tc.name)
        output_preview = (output[:200] if isinstance(output, str) else str(output))[:200]
        # result 字段发完整 output（上限保护防大文件撑爆 SSE/DB）:
        # 前端 ToolCallBlock / 右侧面板 (extractLatestBacktestMetrics) 需要
        # 解析完整 JSON（如 run_backtest 的 metrics）；preview 保持 200 截断
        # 兼容旧消费方。projector 持久化 event.data.result → DB 同样完整。
        output_full = (output if isinstance(output, str) else str(output))[:_TOOL_RESULT_MAX]
        self._emit("tool_result", {
            "tool": tc.name,
            "id": tc.id,                # frontend reads data.id
            "call_id": tc.id,           # backward compat
            "status": status_str,
            "ok": not is_error,          # backward compat
            "result": output_full,       # frontend reads data.result (完整)
            "preview": output_preview,   # backward compat (截断)
            "elapsed_ms": elapsed_ms,
        })

        # Trace tool result
        self._trace({
            "type": "tool_result",
            "tool": tc.name,
            "call_id": tc.id,
            "status": status_str,
            "iteration": getattr(self, "_current_iter", 0),
            "elapsed_ms": elapsed_ms,
            "output_preview": output_preview,
        })

        # Truncate tool output for LLM history (keep head + tail, mark middle)
        history_output = output
        if (
            _TOOL_RESULT_HISTORY_MAX > 0
            and isinstance(output, str)
            and len(output) > _TOOL_RESULT_HISTORY_MAX
        ):
            half = _TOOL_RESULT_HISTORY_MAX // 2
            history_output = (
                output[:half]
                + f"\n\n[truncated {len(output) - _TOOL_RESULT_HISTORY_MAX} chars]\n\n"
                + output[-half:]
            )

        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": history_output,
        }

    def _execute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute one tool_call via the registry; return tool-result message.

        Sync entry over the shared core (see ``_execute_tool_call_core``).
        """
        return _run_coro_in_sync(
            self._execute_tool_call_core(tc, result, async_mode=False)
        )

    async def _aexecute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_call (permission-gated ainvoke)."""
        return await self._execute_tool_call_core(tc, result, async_mode=True)

    async def _execute_tool_with_heartbeat_core(
        self, tc: ToolCall, result: LoopResult, *, async_mode: bool
    ) -> dict[str, Any]:
        """Shared heartbeat wrapper (single source for both modes)."""
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
            return await self._execute_tool_call_core(tc, result, async_mode=async_mode)

    def _execute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute tool_call with HeartbeatTimer for long-running tools.

        Sync entry over the shared core (see
        ``_execute_tool_with_heartbeat_core``).
        """
        return _run_coro_in_sync(
            self._execute_tool_with_heartbeat_core(tc, result, async_mode=False)
        )

    async def _aexecute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_with_heartbeat."""
        return await self._execute_tool_with_heartbeat_core(tc, result, async_mode=True)

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
        """Apply context compression if over threshold (sync path).

        Single source: the shared sync core is ``_maybe_compact_impl``;
        the async twin (``_amaybe_compact``) runs the same core in a
        worker thread so the event loop is never blocked.

        opencode-aligned trigger formula:
            trigger = threshold_tokens (default derived from model
            context: context - max(model_max_output, buffer))

        If L4 fails (e.g. DB error during persistence), the
        compaction is rolled back and the original messages are
        kept. The LLM doesn't lose context.
        """
        return self._maybe_compact_impl(
            messages, run_compact=self._run_compact_messages
        )

    def _run_compact_messages(self, messages: list[dict[str, Any]]):
        """Invoke the compact_messages engine with the loop's config."""
        return compact_messages(
            messages,
            config=self.cc,
            threshold_tokens=self.threshold_tokens,
            model_context_tokens=self.config.model_context_tokens,
            model_max_output_tokens=self.config.model_max_output_tokens,
            llm_client=self.client,
            previous_summary=self._previous_summary,
            session_id=self.session_id,
        )

    def _maybe_compact_impl(
        self,
        messages: list[dict[str, Any]],
        *,
        run_compact: Callable[[list[dict[str, Any]]], tuple],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Shared sync compaction core (single source for both modes).

        ``run_compact`` is the only divergent point: the sync path calls
        it directly on the calling thread, the async path runs this whole
        core in a worker thread (``asyncio.to_thread``) so the loop is
        never blocked by the sync LLM summary call.
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
            messages, applied, l4_summary_text, l4_recent_text = run_compact(messages)
        except Exception:
            # Critical: L4 failed. Roll back to keep full history.
            # The LLM is more useful with full history than with
            # partial compaction.
            logger.exception("L4 compaction failed; keeping full history")
            return original_messages, []

        if l4_summary_text and any(layer.startswith("llm_summarize") for layer in applied):
            self._previous_summary = l4_summary_text
            try:
                self._persist_compaction_event(
                    l4_summary_text, l4_recent_text or "",
                    compressed_messages=messages,
                )
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
        compressed_messages: list[dict] | None = None,
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
            compressed_messages: Post-L4 messages list (system + recent).
                When provided AND the ``SR_L4_INCLUDE_MESSAGES`` env
                var is truthy, this list is included in the
                ``compact.ended`` event so the projector can replace
                the previous messages table contents — keeping the DB
                consistent with the in-memory state already updated
                by ``compact_messages``.  When the flag is off (the
                default), the previous behaviour is preserved: only a
                marker is emitted, original messages stay in DB.

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
                #
                # Two modes (controlled by SR_L4_INCLUDE_MESSAGES):
                # 1. Default: emit only a marker. The DB keeps the
                #    original messages; the marker records that
                #    compaction happened.  In-memory state has
                #    already been compressed by compact_messages, so
                #    the LLM sees the compressed view, but a fresh
                #    reload from the DB would see the originals.
                # 2. Flag enabled: include the post-L4 messages list
                #    so the projector replaces the DB contents with
                #    the compressed view.  This makes the DB
                #    consistent with the in-memory state, matching
                #    what manual /compact already does.
                import os

                include_msgs = (
                    compressed_messages is not None
                    and os.environ.get("SR_L4_INCLUDE_MESSAGES", "").lower()
                    in ("1", "true", "yes")
                )
                payload: dict[str, Any] = {
                    "summary": comp.summary,
                    "reason": "auto",
                    "compaction_id": comp.id,
                    "metadata": comp.metadata,
                }
                if include_msgs:
                    payload["messages"] = compressed_messages
                    # opencode-aligned: the projector infers the
                    # compaction boundary (compacted_until_seq) from
                    # the compressed message list + projection order.
                self._event_bus.emit(
                    session_id,
                    "compact.ended",
                    payload,
                )
                logger.info(
                    "compaction event emitted: %s (summary=%d chars, "
                    "recent=%d chars, include_messages=%s)",
                    comp.id, len(comp.summary), len(comp.recent),
                    include_msgs,
                )
            else:
                # Legacy fallback: direct DB write via the registered
                # persister (registered by api/ and TUI entry points).
                #
                # Fail-fast: if no persister is registered, raise.
                # Previously this path silently dropped the compaction
                # event, which made misconfigured deployments invisible
                # to operators. Now the misconfiguration is surfaced
                # immediately at runtime.
                if _compaction_persister is None:
                    raise RuntimeError(
                        f"compaction_persister not registered; cannot "
                        f"persist compaction event {comp.id}. Register "
                        f"via register_compaction_persister() in the "
                        f"api/cli entry point."
                    )
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
        """Async version of _maybe_compact — runs the shared sync core
        (``_maybe_compact_impl``) in a worker thread so the event loop is
        never blocked.

        Acquires per-session compact lock to prevent concurrent compaction
        (auto from agent loop + manual /compact) on the same session.
        The sync LLM client (``self.client.chat``) is invoked directly
        from the worker thread, with no nested event loop.
        """
        if not self.session_id:
            return await asyncio.to_thread(
                self._maybe_compact_impl, messages, run_compact=self._run_compact_messages
            )
        from .compact import _compact_locks
        lock = await _compact_locks.get(self.session_id)
        async with lock:
            return await asyncio.to_thread(
                self._maybe_compact_impl, messages, run_compact=self._run_compact_messages
            )

    # ── Trace helpers ──────────────────────────────

    def _trace(self, entry: dict[str, Any]) -> None:
        """Write a trace entry if trace writer is active."""
        if self._trace_writer is not None:
            try:
                self._trace_writer.write(entry)
            except Exception:                       # noqa: BLE001
                pass  # trace failures should never break the loop

    def _trace_llm_request(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Log full LLM request envelope (DSH request/header pattern).

        Records system prompt, tools schema, and history metadata for each
        LLM call.  Large fields (system prompt, tools JSON) are offloaded to
        sidecar files when they exceed the threshold.

        Two sinks:
        1. ``llm_request`` on_event → the B4 forwarder (service.py) offloads
           large fields and persists to event_log (single source of truth).
        2. trace.jsonl (optional, backward-compat) if a trace_writer is set.

        This provides the foundation for Trajectory View / Trace Viewer.
        """
        try:
            import json as _json

            # Extract system prompt from first message
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = messages[0].get("content", "")

            # Tools schema as JSON
            tools_json = _json.dumps(tools, ensure_ascii=False) if tools else "[]"

            # History metadata (don't log full content — too large)
            history_meta = []
            for m in messages[1:]:  # skip system prompt
                history_meta.append({
                    "role": m.get("role", "?"),
                    "content_len": len(m.get("content", "")),
                    "has_tool_calls": bool(m.get("tool_calls")),
                })

            entry: dict[str, Any] = {
                "type": "llm_request",
                "iteration": iteration,
                "session_id": self.session_id or "",
                "history_count": len(messages),
                "history_meta": history_meta,
                "tools_count": len(tools) if tools else 0,
                "system_prompt_len": len(system_prompt),
                "system_prompt": system_prompt,
                "tools_schema": tools_json,
            }

            # Sink 1: event_log via the on_event forwarder (offloads large
            # fields itself). Best-effort — never break the loop.
            try:
                self._emit("llm_request", dict(entry))
            except Exception:  # noqa: BLE001
                logger.debug("llm_request emit failed", exc_info=True)

            # Sink 2: trace.jsonl (optional, backward-compat).
            if self._trace_writer is not None:
                try:
                    # Use sidecar offload for large fields
                    self._trace_writer.write_text_entry(
                        dict(entry),
                        field="system_prompt",
                        value=system_prompt,
                        offload_kind=f"llm-request-system-{iteration}",
                    )
                    # Tools schema goes inline (usually small) or offloaded
                    self._trace_writer.write_text_entry(
                        dict(entry),
                        field="tools_schema",
                        value=tools_json,
                        offload_kind=f"llm-request-tools-{iteration}",
                        threshold=10_000,  # tools schema usually fits inline
                    )
                except Exception:  # noqa: BLE001
                    pass  # trace failures should never break the loop
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
            return _get_goal_store().get_current_snapshot(self.session_id)
        except Exception:  # noqa: BLE001
            return None

    def _inject_todos_snapshot(self, messages: list[dict[str, Any]]) -> None:
        """Append a <current-todos> system block when the session has todos.

        Injects only when the snapshot changed since the last injection
        (tracked via hash) so we don't spam identical system messages.
        """
        if not self.session_id:
            return
        try:
            from .builtin_tools.todo_tools import TodoStore, _format_todos_snapshot
            todos = TodoStore.get(self.session_id)
        except Exception:  # noqa: BLE001
            return
        if not todos:
            return
        block = _format_todos_snapshot(todos)
        h = hash(block)
        if getattr(self, "_last_todos_hash", None) == h:
            return
        self._last_todos_hash = h
        messages.append({"role": "system", "content": block})

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
