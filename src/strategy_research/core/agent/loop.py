"""AgentLoop: ReAct-style agent execution loop.

Minimal version (PR6-c2):
    - Builds initial messages via ContextBuilder
    - Calls LLM (OpenAICompatClient.chat)
    - Executes tool_calls in order
    - Returns LoopResult when LLM stops or max_iterations reached
    - Detects "no_progress" (last 3 tool_calls hashes identical)

NOT in this version (PR6-c3):
    - 5-layer context compression
    - HeartbeatTimer for long tool calls
    - TraceWriter integration
    - git commit after run

NOT in this PR (PR7):
    - Tool dispatch optimizations
    - Cancellation tokens
    - Checkpointing
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..llm import LLMConfig, LLMResponse, OpenAICompatClient, ToolCall
from ..llm.errors import LLMError
from ..memory.persistent import PersistentMemory
from .context import ContextBuilder, estimate_tokens
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


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
    ):
        self.config = config
        self.registry = registry
        self.memory = memory
        self.workspace = workspace
        self.max_iterations = max_iterations
        self.no_progress_window = no_progress_window
        self.context_builder = ContextBuilder(
            config=config, registry=registry,
            memory=memory, workspace=workspace,
        )
        self.client = OpenAICompatClient(config)
        # Track tool_calls per iteration for no_progress detection
        self._recent_hashes: list[str] = []

    # ── Public API ───────────────────────────────

    def run(self, task: str) -> LoopResult:
        """Run the loop until done.

        Args:
            task: User task description.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        result = LoopResult()
        messages = self.context_builder.build_initial_messages(task)
        result.messages = list(messages)  # shallow copy for inspection

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration

            try:
                response = self.client.chat(messages)
            except LLMError as exc:
                result.finished_reason = "error"
                result.error = f"{type(exc).__name__}: {exc}"
                logger.warning("LLM error at iter %d: %s", iteration, exc)
                break

            # Append assistant message (preserve tool_calls structure)
            assistant_msg = self._response_to_assistant_msg(response)
            messages.append(assistant_msg)
            result.messages.append(assistant_msg)

            # No tool_calls → final answer
            if not response.has_tool_calls():
                result.answer = response.content
                result.finished_reason = "stop"
                return result

            # Execute each tool_call
            tool_hashes_this_iter: list[str] = []
            for tc in response.tool_calls:
                tool_hashes_this_iter.append(_tool_call_hash(tc))
                tool_result_msg = self._execute_tool_call(tc, result)
                messages.append(tool_result_msg)
                result.messages.append(tool_result_msg)

            # No-progress detection
            self._recent_hashes.extend(tool_hashes_this_iter)
            if len(self._recent_hashes) > self.no_progress_window:
                self._recent_hashes = self._recent_hashes[-self.no_progress_window:]
            if self._detect_no_progress():
                result.finished_reason = "no_progress"
                # Extract any text content from last assistant msg
                result.answer = (response.content or "").strip()
                if not result.answer:
                    result.answer = (
                        f"No progress detected (last {self.no_progress_window} "
                        f"tool calls identical)"
                    )
                return result

        # Exceeded max_iterations (only if not already terminated)
        if result.finished_reason == "stop" and not result.answer:
            result.finished_reason = "max_iter"
            result.answer = (
                f"Reached max_iterations={self.max_iterations} without "
                f"a final answer. Last LLM response: "
                f"{(result.messages[-2].get('content') or '')[:200] if len(result.messages) >= 2 else ''}"
            ).strip()
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
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    {"status": "error", "error": f"tool '{tc.name}' not found"},
                    ensure_ascii=False,
                ),
            }

        # Inject workspace kwarg if not present
        kwargs = dict(tc.arguments)
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace

        try:
            output = tool.execute(**kwargs)
        except Exception as exc:                    # noqa: BLE001
            logger.exception("tool %s raised", tc.name)
            output = json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        }

    def _detect_no_progress(self) -> bool:
        """Return True if last N tool_calls all have the same hash."""
        if len(self._recent_hashes) < self.no_progress_window:
            return False
        window = self._recent_hashes[-self.no_progress_window:]
        return len(set(window)) == 1


__all__ = ["AgentLoop", "LoopResult"]