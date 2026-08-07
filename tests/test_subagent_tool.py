"""Tests for SubAgentTool — chat-mode subagent delegation.

Covers:
  * Chat generates 2 subagents in one turn (parent AgentLoop calls
    delegate_to_agent twice, each spawns a child SwarmWorker).
  * SubAgentTool count limit (MAX_SUBAGENTS = 5) enforced.
  * No nested delegation — the child registry excludes delegate_to_agent.
  * subagent_* SSE events emitted with distinct agent_ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.builtin_tools.subagent_tool import (
    MAX_SUBAGENTS,
    SubAgentTool,
)
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.core.llm import LLMConfig, LLMResponse, ToolCall


# ── Helpers ──────────────────────────────────────────────────────────


class AsyncMockLLM:
    """Mock parent LLM — returns queued LLMResponse from async achat()."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    async def achat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("AsyncMockLLM exhausted")
        return self.responses.pop(0)


class SyncMockLLM:
    """Mock child LLM — returns queued LLMResponse from sync chat().

    Used as a stand-in for the OpenAICompatClient that SubAgentTool
    constructs internally (patched via monkeypatch).
    """

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("SyncMockLLM exhausted")
        return self.responses.pop(0)


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def delegate_resp(task: str, call_id: str) -> LLMResponse:
    tc = ToolCall(id=call_id, name="delegate_to_agent", arguments={"task": task})
    return LLMResponse(content="", tool_calls=[tc], finish_reason="tool_calls")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_child_client(monkeypatch):
    """Make SubAgentTool's internal OpenAICompatClient return a SyncMockLLM.

    Yields a ``make(child_responses)`` callable that patches the module so
    each SubAgentTool.execute() creates a child client with those responses,
    and returns the list of created clients for assertions.
    """

    def make(child_responses: list[LLMResponse]) -> list[SyncMockLLM]:
        created: list[SyncMockLLM] = []

        def _factory(_config):
            mock = SyncMockLLM(list(child_responses))
            created.append(mock)
            return mock

        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.OpenAICompatClient",
            _factory,
        )
        # Avoid hitting real LLMConfig.load() (env/disk).
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.LLMConfig",
            MagicMock(load=MagicMock(return_value=MagicMock())),
        )
        return created

    return make


def _collect_events(loop: AgentLoop) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    loop._on_event = lambda et, data: events.append((et, data or {}))
    return events


# ── Chat generates 2 subagents ───────────────────────────────────────


class TestChatGeneratesSubagents:
    @pytest.mark.asyncio
    async def test_chat_spawns_two_subagents(self, workspace, patch_child_client):
        """Parent agent calls delegate_to_agent twice → 2 child agents run.

        The parent LLM is mocked to emit two delegate_to_agent tool calls
        then a final text answer. Each child (patched OpenAICompatClient)
        responds with its own text, so 2 subagents complete with distinct
        agent_ids and both emit subagent_started + subagent_completed.
        """
        child_clients = patch_child_client([text_resp("subagent A result")])

        parent = AsyncMockLLM([
            delegate_resp("研究动量因子", call_id="call-a"),
            delegate_resp("分析回撤", call_id="call-b"),
            text_resp("全部完成"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("帮我做两个分析")

        # Parent loop: 2 tool calls made, final answer reached
        assert r.tool_calls_made == 2
        assert r.answer == "全部完成"
        assert r.success

        # 2 child clients created (one per delegation)
        assert len(child_clients) == 2
        assert all(len(c.calls) == 1 for c in child_clients)

        # subagent lifecycle events: 2 started, 2 completed, distinct ids
        started = [e for e in events if e[0] == "subagent_started"]
        completed = [e for e in events if e[0] == "subagent_completed"]
        assert len(started) == 2
        assert len(completed) == 2
        agent_ids = {e[1]["agent_id"] for e in started}
        assert len(agent_ids) == 2  # distinct agent_ids
        assert all(e[1]["agent_id"] in agent_ids for e in completed)

        # Each tool result contains the subagent's answer
        tool_results = [
            e[1] for e in events if e[0] == "tool_result" and e[1].get("tool") == "delegate_to_agent"
        ]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert tr["status"] == "done"
            assert "subagent A result" in tr["result"]

    @pytest.mark.asyncio
    async def test_chat_single_subagent_single_loop(self, workspace, patch_child_client):
        """A single delegation is enough to complete the turn."""
        patch_child_client([text_resp("lone subagent")])
        parent = AsyncMockLLM([
            delegate_resp("单一子任务", call_id="call-1"),
            text_resp("完成"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("委派一个任务")
        assert r.tool_calls_made == 1
        assert r.answer == "完成"
        started = [e for e in events if e[0] == "subagent_started"]
        assert len(started) == 1
        assert started[0][1]["message_id"] is None  # loop has no message_id


# ── Constraint: count limit ──────────────────────────────────────────


class TestSubAgentCountLimit:
    def test_max_five_delegations(self, monkeypatch):
        """SubAgentTool refuses beyond MAX_SUBAGENTS delegations per turn."""
        # Patch the child client so the first MAX_SUBAGENTS calls succeed
        # deterministically (no real LLM config / network).
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.OpenAICompatClient",
            lambda _config: SyncMockLLM([text_resp("ok")]),
        )
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.LLMConfig",
            MagicMock(load=MagicMock(return_value=MagicMock())),
        )

        tool = SubAgentTool()
        count_ref = [0]

        for i in range(MAX_SUBAGENTS):
            out = tool.execute(task=f"task {i}", _subagent_count_ref=count_ref, emit_event=None)
            assert count_ref[0] == i + 1
            parsed = json.loads(out)
            assert parsed["status"] == "ok"
            assert parsed["answer"] == "ok"

        # 6th delegation is refused BEFORE any child client is built —
        # the actionable error directs the user to the workflow path.
        out = tool.execute(task="overflow", _subagent_count_ref=count_ref, emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "error"
        assert "最多委派" in parsed["error"]
        assert "工作流" in parsed["error"]
        assert count_ref[0] == MAX_SUBAGENTS  # not incremented past the cap

    def test_count_ref_increments_even_without_client(self, monkeypatch):
        """Counter increments before child client construction."""
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.OpenAICompatClient",
            lambda _config: SyncMockLLM([text_resp("ok")]),
        )
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.subagent_tool.LLMConfig",
            MagicMock(load=MagicMock(return_value=MagicMock())),
        )
        tool = SubAgentTool()
        count_ref = [0]
        tool.execute(task="x", _subagent_count_ref=count_ref, emit_event=None)
        assert count_ref[0] == 1


# ── Constraint: no nested delegation ─────────────────────────────────


class TestNoNestedDelegation:
    def test_child_registry_excludes_delegate(self):
        """Sub-agent's tool registry never contains delegate_to_agent."""
        tool = SubAgentTool()
        parent_registry = build_default_registry()
        child_registry = tool._build_child_registry(parent_registry, None)
        assert isinstance(child_registry, ToolRegistry)
        assert child_registry.get("delegate_to_agent") is None
        # Sanity: other tools still present
        assert child_registry.get("read_file") is not None

    def test_child_registry_applies_whitelist(self):
        tool = SubAgentTool()
        parent_registry = build_default_registry()
        child = tool._build_child_registry(parent_registry, ["read_file", "write_file"])
        assert child.get("read_file") is not None
        assert child.get("write_file") is not None
        assert child.get("run_backtest") is None
        assert child.get("delegate_to_agent") is None
