"""SwarmWorker — mini-ReAct loop for swarm agents (P6 Phase 1-A1).

A lightweight, single-purpose ReAct loop designed for swarm nodes. It is
NOT a replacement for ``AgentLoop``; it intentionally drops many features
(sessions, memory recall, hooks, git auto-commit) to keep the per-node
overhead low. The full feature set lives in ``AgentLoop`` for the
single-agent path.

Design choices (inspired by vibe-trading ``src/swarm/worker.py``):
    * Direct ``client.chat`` calls (no AgentLoop recursion)
    * Tool whitelist applied at runtime (``registry.with_whitelist``)
    * KEEP_RECENT_TOOLS=3 microcompact after that many tool calls
    * At 0.8 × max_iterations: inject wrap-up nudge
    * Final iteration: tools=None (force text)
    * 60k token budget hard cut (token_limit status)
    * timeout per iteration (timeout status)
    * network errors → WorkerStatus.failed (no infinite retry here)

WorkerStatus enum (matches vibe-trading):
    completed | failed | timeout | token_limit | cancelled

The worker is **stateless** except for the live messages list; safe to
construct fresh per task.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..llm import LLMResponse, OpenAICompatClient, ToolCall
from ..llm.errors import LLMError
from ..agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


# ── Public constants ──────────────────────────────────────────────────


KEEP_RECENT_TOOLS = 3
WRAP_UP_RATIO = 0.8
TOKEN_LIMIT_CHARS = 60_000  # ~ 15k tokens; cheap heuristic


class WorkerStatus(str, Enum):
    """Final state of a SwarmWorker run."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    TOKEN_LIMIT = "token_limit"
    CANCELLED = "cancelled"


# ── Result dataclass ─────────────────────────────────────────────────


@dataclass
class WorkerResult:
    """Outcome of a single SwarmWorker.run() invocation."""

    status: WorkerStatus = WorkerStatus.COMPLETED
    answer: str = ""
    iterations: int = 0
    tool_calls_made: int = 0
    summary: str = ""           # ≤ 2 sentences, mandated by prompt
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status in (WorkerStatus.COMPLETED,)


# ── Helpers ──────────────────────────────────────────────────────────


def _estimate_chars(messages: list[dict[str, Any]]) -> int:
    """Cheap char-count proxy for token estimation (4 chars ≈ 1 token)."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def _microcompact_tool_results(messages: list[dict[str, Any]]) -> None:
    """Trim old tool messages; keep last KEEP_RECENT_TOOLS verbatim.

    Mutates ``messages`` in place.
    """
    tool_indices: list[int] = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool"
    ]
    if len(tool_indices) <= KEEP_RECENT_TOOLS:
        return
    to_trim = tool_indices[:-KEEP_RECENT_TOOLS]
    for i in to_trim:
        content = messages[i].get("content")
        if isinstance(content, str) and len(content) > 200:
            messages[i]["content"] = content[:200] + "…[trimmed]"


# ── SwarmWorker ──────────────────────────────────────────────────────


class SwarmWorker:
    """Mini-ReAct loop for a single swarm agent.

    Parameters
    ----------
    client:
        An ``OpenAICompatClient`` (or duck-typed object exposing ``.chat``).
    registry:
        A ``ToolRegistry`` (must support ``with_whitelist`` or fall back
        to the unfiltered registry).
    system_prompt:
        Full system prompt (caller composes role + skills + grounding).
    upstream_context:
        Pre-formatted summary block from upstream agents. Injected as a
        system-prompt appendix on the first iteration.
    max_iterations:
        Hard cap on chat rounds. Default 20.
    timeout_s:
        Per-iteration wall-clock cap. Whole run may exceed this if a
        tool call hangs; we treat per-iteration timeouts as TIMEOUT status.
    temperature, max_tokens:
        Passed through to ``client.chat``.

    The worker is single-shot; create a new one per task. It does not
    retain state between ``run()`` calls.
    """

    def __init__(
        self,
        client: Any,
        registry: ToolRegistry,
        system_prompt: str,
        upstream_context: str = "",
        max_iterations: int = 20,
        timeout_s: float = 60.0,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> None:
        self._client = client
        self._registry = registry
        self._system_prompt = system_prompt
        self._upstream_context = upstream_context
        self._max_iterations = max_iterations
        self._timeout_s = timeout_s
        self._temperature = temperature
        self._max_tokens = max_tokens

    # ── Public API ─────────────────────────────────────────────

    def run(self, task: str) -> WorkerResult:
        """Execute one mini-ReAct loop. Returns ``WorkerResult``.

        Catches every exception internally and converts to a non-COMPLETED
        ``WorkerStatus``. Never raises (except programmer errors).
        """
        messages = self._build_initial_messages(task)
        tool_calls_made = 0
        wrap_up_iter = max(1, int(self._max_iterations * WRAP_UP_RATIO))

        for it in range(self._max_iterations):
            # Token / size guard
            if _estimate_chars(messages) > TOKEN_LIMIT_CHARS:
                return self._terminate(
                    WorkerStatus.TOKEN_LIMIT,
                    messages, it, tool_calls_made,
                    reason=f"messages > {TOKEN_LIMIT_CHARS} chars",
                )

            # Final iteration: force text (no tool calls)
            tool_defs = None if it == self._max_iterations - 1 else self._registry.get_definitions()

            # Wrap-up nudge
            if it == wrap_up_iter:
                messages.append({
                    "role": "system",
                    "content": (
                        "Wrap-up: 2 sentences max. If you must write a file, "
                        "do so now via write_file (if available). End your reply "
                        "with a clear final summary."
                    ),
                })

            # Per-iteration timeout
            t0 = time.perf_counter()
            try:
                response: LLMResponse = self._client.chat(
                    messages,
                    tools=tool_defs,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
            except LLMError as exc:
                return self._terminate(
                    WorkerStatus.FAILED,
                    messages, it, tool_calls_made,
                    reason=f"llm error: {exc}",
                )
            except Exception as exc:                                # noqa: BLE001
                return self._terminate(
                    WorkerStatus.FAILED,
                    messages, it, tool_calls_made,
                    reason=f"chat() raised: {exc}",
                )

            if (time.perf_counter() - t0) > self._timeout_s:
                return self._terminate(
                    WorkerStatus.TIMEOUT,
                    messages, it, tool_calls_made,
                    reason=f"iter {it} exceeded {self._timeout_s}s",
                )

            content = (response.content or "").strip()
            tool_calls: list[ToolCall] = response.tool_calls or []

            # Append assistant message
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            # No tool calls → done
            if not tool_calls:
                return self._terminate(
                    WorkerStatus.COMPLETED,
                    messages, it + 1, tool_calls_made,
                    reason="llm stopped",
                    answer=content,
                )

            # Execute tools
            for tc in tool_calls:
                tool_result = self._registry.execute(tc.name, tc.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": tool_result,
                })
                tool_calls_made += 1

            # Microcompact after enough tools
            if tool_calls_made > KEEP_RECENT_TOOLS:
                _microcompact_tool_results(messages)

        # Loop exited naturally (max_iterations reached)
        return self._terminate(
            WorkerStatus.COMPLETED,
            messages, self._max_iterations, tool_calls_made,
            reason="max_iterations",
            answer=messages[-1].get("content", "") if messages else "",
        )

    # ── Internals ─────────────────────────────────────────────

    def _build_initial_messages(self, task: str) -> list[dict[str, Any]]:
        """Compose system + (optional upstream context) + user prompt."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        if self._upstream_context:
            messages.append({
                "role": "system",
                "content": f"Upstream agent outputs:\n\n{self._upstream_context}",
            })
        messages.append({"role": "user", "content": task})
        return messages

    def _terminate(
        self,
        status: WorkerStatus,
        messages: list[dict[str, Any]],
        iterations: int,
        tool_calls_made: int,
        reason: str = "",
        answer: str = "",
    ) -> WorkerResult:
        """Build final WorkerResult and extract summary."""
        if not answer:
            # Try last assistant content
            for m in reversed(messages):
                if m.get("role") == "assistant":
                    answer = m.get("content", "") or ""
                    break

        # Summary = first 2 sentences of answer
        summary = _first_two_sentences(answer)

        return WorkerResult(
            status=status,
            answer=answer,
            iterations=iterations,
            tool_calls_made=tool_calls_made,
            summary=summary,
            messages=messages,
            error=reason if status != WorkerStatus.COMPLETED else None,
        )


def _first_two_sentences(text: str) -> str:
    """Return at most the first 2 sentences of text."""
    text = (text or "").strip()
    if not text:
        return ""
    import re
    # First try splitting on whitespace after sentence terminators
    # (English-style ". foo"). Fall back to splitting directly on CJK
    # terminators (Chinese-style "。句。" with no space).
    parts = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=2)
    if len(parts) >= 2:
        return " ".join(parts[:2]).strip()[:400]
    # No whitespace split possible — split on the terminators themselves
    cjk_parts = re.split(r"(?<=[.!?。！？])", text, maxsplit=2)
    if len(cjk_parts) >= 2:
        return "".join(cjk_parts[:2]).strip()[:400]
    return text[:400]


__all__ = [
    "SwarmWorker",
    "WorkerResult",
    "WorkerStatus",
    "KEEP_RECENT_TOOLS",
    "WRAP_UP_RATIO",
    "TOKEN_LIMIT_CHARS",
]