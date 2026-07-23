"""Tests for SwarmWorker mini-ReAct + WorkflowController.execute_agent (P6 Phase 1)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.core.llm import LLMResponse, ToolCall
from strategy_research.core.llm.errors import LLMAuthError, LLMRateLimitError, LLMTimeoutError
from strategy_research.core.workflow.controller import (
    ControllerConfig,
    WorkflowController,
)
from strategy_research.core.workflow.types import AgentCall, AgentStatus
from strategy_research.core.workflow.worker import (
    KEEP_RECENT_TOOLS,
    TOKEN_LIMIT_CHARS,
    SwarmWorker,
    WorkerResult,
    WorkerStatus,
    _first_two_sentences,
    _microcompact_tool_results,
)


# ── Helpers ──────────────────────────────────────────────────────────


class MockLLM:
    """Simple mock that returns queued LLMResponse objects (mimics OpenAICompatClient)."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []
        self.last_messages: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        self.last_messages = list(messages)
        if not self.responses:
            raise RuntimeError("MockLLM exhausted; no more responses queued")
        return self.responses.pop(0)


def text_resp(content: str, **kwargs) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop", **kwargs)


def tool_resp(name: str, args: dict, content: str = "ok", **kwargs) -> LLMResponse:
    tc = ToolCall(id="call_1", name=name, arguments=args)
    return LLMResponse(
        content=content,
        tool_calls=[tc],
        finish_reason="tool_calls",
        **kwargs,
    )


# ── SwarmWorker basic loop ──────────────────────────────────────────


class TestSwarmWorkerBasic:
    def test_completes_on_first_text_response(self):
        mock = MockLLM([text_resp("Final answer: 42")])
        registry = ToolRegistry()
        worker = SwarmWorker(
            client=mock, registry=registry,
            system_prompt="You are a test agent.",
        )
        result = worker.run("say something")
        assert result.status == WorkerStatus.COMPLETED
        assert result.answer == "Final answer: 42"
        assert result.iterations == 1
        assert result.tool_calls_made == 0
        assert result.success is True

    def test_tool_call_then_text_completes(self):
        mock = MockLLM([
            tool_resp("noop_tool", {}, content="thinking"),
            text_resp("done"),
        ])

        class NoopTool:
            name = "noop_tool"
            description = "no-op"
            parameters = {"type": "object", "properties": {}, "required": []}
            is_readonly = True

            def execute(self, **kwargs):
                return json.dumps({"status": "ok"})

            def to_openai_schema(self):
                return {
                    "type": "function",
                    "function": {"name": self.name, "description": self.description,
                                 "parameters": self.parameters},
                }

        registry = ToolRegistry()
        registry.register(NoopTool())
        worker = SwarmWorker(client=mock, registry=registry, system_prompt="x")
        result = worker.run("task")
        assert result.status == WorkerStatus.COMPLETED
        assert result.tool_calls_made == 1
        assert result.iterations == 2

    def test_max_iterations_terminates(self):
        # Always return tool_calls → never "stops" → exhausts max_iterations
        mock = MockLLM([
            tool_resp("noop", {}) for _ in range(5)
        ])

        class NoopTool:
            name = "noop"
            description = "n"
            parameters = {"type": "object", "properties": {}, "required": []}

            def execute(self, **kwargs):
                return json.dumps({"ok": True})

            def to_openai_schema(self):
                return {"type": "function", "function": {"name": "noop", "description": "n", "parameters": self.parameters}}

        registry = ToolRegistry()
        registry.register(NoopTool())
        worker = SwarmWorker(client=mock, registry=registry, system_prompt="x", max_iterations=3)
        result = worker.run("loop forever")
        assert result.iterations == 3
        assert result.status == WorkerStatus.COMPLETED  # graceful exit on max_iter


# ── SwarmWorker error handling ──────────────────────────────────────


class TestSwarmWorkerErrors:
    def test_llm_error_returns_failed(self):
        class FailLLM:
            def chat(self, *args, **kwargs):
                raise LLMAuthError("bad key")

        worker = SwarmWorker(client=FailLLM(), registry=ToolRegistry(), system_prompt="x")
        result = worker.run("anything")
        assert result.status == WorkerStatus.FAILED
        assert "bad key" in (result.error or "")

    def test_unexpected_exception_returns_failed(self):
        class WeirdLLM:
            def chat(self, *args, **kwargs):
                raise ValueError("weird bug")

        worker = SwarmWorker(client=WeirdLLM(), registry=ToolRegistry(), system_prompt="x")
        result = worker.run("anything")
        assert result.status == WorkerStatus.FAILED
        assert "weird bug" in (result.error or "")

    def test_token_limit_status_when_messages_explode(self):
        # Build a worker with a huge system prompt to inflate token budget
        # then return text immediately so it terminates with TOKEN_LIMIT status.
        huge = "x" * (TOKEN_LIMIT_CHARS + 1000)
        mock = MockLLM([text_resp("big answer")])
        worker = SwarmWorker(client=mock, registry=ToolRegistry(), system_prompt=huge)
        result = worker.run("task")
        assert result.status == WorkerStatus.TOKEN_LIMIT


# ── SwarmWorker microcompact ─────────────────────────────────────────


class TestMicrocompact:
    def test_old_tool_results_truncated(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 1000},
            {"role": "tool", "tool_call_id": "t2", "content": "x" * 1000},
            {"role": "tool", "tool_call_id": "t3", "content": "x" * 1000},
            {"role": "tool", "tool_call_id": "t4", "content": "x" * 1000},
            {"role": "tool", "tool_call_id": "t5", "content": "x" * 1000},
        ]
        _microcompact_tool_results(messages)
        # Last KEEP_RECENT_TOOLS (3) should be intact (still 1000 chars each)
        for i in (-1, -2, -3):
            assert len(messages[i]["content"]) == 1000  # untouched
        # Older tool messages (t1, t2 at indices 2, 3) should be truncated
        assert "[trimmed]" in messages[2]["content"]
        assert "[trimmed]" in messages[3]["content"]
        assert len(messages[2]["content"]) <= 250

    def test_below_threshold_untouched(self):
        messages = [
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 1000},
            {"role": "tool", "tool_call_id": "t2", "content": "x" * 1000},
        ]
        before = [m["content"] for m in messages]
        _microcompact_tool_results(messages)
        assert [m["content"] for m in messages] == before


# ── _first_two_sentences ─────────────────────────────────────────────


class TestFirstTwoSentences:
    def test_short_text(self):
        assert _first_two_sentences("Hello world.") == "Hello world."

    def test_three_sentences(self):
        text = "First. Second. Third."
        out = _first_two_sentences(text)
        assert "First" in out
        assert "Second" in out
        assert "Third" not in out

    def test_chinese_punctuation(self):
        text = "第一句。第二句。第三句。"
        out = _first_two_sentences(text)
        assert "第一句" in out
        assert "第二句" in out
        assert "第三句" not in out

    def test_empty(self):
        assert _first_two_sentences("") == ""
        assert _first_two_sentences("   ") == ""


# ── WorkflowController.execute_agent ─────────────────────────────────


class TestExecuteAgent:
    def _make_controller(self) -> WorkflowController:
        cfg = ControllerConfig(timeout_seconds=10.0)
        return WorkflowController(registry=MagicMock(), adj={}, config=cfg)

    def _patch_default_controller_factory(self, monkeypatch, controller):
        """Make ``_build_default_controller`` return a fixed controller."""
        import strategy_research.core.swarm.runtime as swarm_runtime
        monkeypatch.setattr(
            swarm_runtime, "_build_default_controller",
            lambda: controller,
        )

    def test_missing_prompt_file_returns_error_json(self, tmp_path):
        ctrl = self._make_controller()
        call = AgentCall(agent_name="ghost", prompt=".prompts/does_not_exist.md")
        out = ctrl.execute_agent(call, "task", workspace=tmp_path)
        data = json.loads(out)
        assert data["status"] == "error"
        assert "prompt resolution failed" in data["error"]

    def test_resolves_prompt_from_templates(self, tmp_path, monkeypatch):
        ctrl = self._make_controller()
        # Build a mock client that returns text immediately
        mock_client = MockLLM([text_resp("ok")])
        monkeypatch.setattr(WorkflowController, "_build_llm_client",
                            lambda self: mock_client)
        # Use a real .prompts file that ships with the package
        call = AgentCall(
            agent_name="researcher",
            prompt=".prompts/researcher.md",
            context={"tools": []},   # empty whitelist → no tools, text only
        )
        out = ctrl.execute_agent(call, "task", workspace=tmp_path)
        data = json.loads(out)
        assert data["agent"] == "researcher"
        assert data["status"] in ("completed", "failed")
        assert "answer" in data

    def test_empty_tool_whitelist_yields_text_only_worker(self, tmp_path, monkeypatch):
        mock_client = MockLLM([text_resp("text-only answer")])
        monkeypatch.setattr(WorkflowController, "_build_llm_client",
                            lambda self: mock_client)
        ctrl = self._make_controller()
        call = AgentCall(
            agent_name="researcher",
            prompt=".prompts/researcher.md",
            context={"tools": []},
        )
        out = ctrl.execute_agent(call, "task", workspace=tmp_path)
        data = json.loads(out)
        assert data["status"] == "completed"
        assert data["answer"] == "text-only answer"

    def test_whitelist_filters_tools(self):
        """Verify only whitelisted tools are in the registry."""
        from strategy_research.core.agent.tools import ToolRegistry
        from strategy_research.core.agent.builtin_tools import build_default_registry

        full = build_default_registry()
        ctrl = self._make_controller()
        filtered = ctrl._build_tool_registry(["read_file"])
        assert "read_file" in filtered
        assert "write_file" not in filtered
        assert len(filtered) == 1
        # Empty whitelist → empty registry (text-only mode)
        empty = ctrl._build_tool_registry([])
        assert len(empty) == 0

    def test_llm_client_init_failure_returns_error(self, tmp_path, monkeypatch):
        def boom(self):
            raise RuntimeError("no API key")
        monkeypatch.setattr(WorkflowController, "_build_llm_client", boom)
        ctrl = self._make_controller()
        call = AgentCall(
            agent_name="x",
            prompt=".prompts/researcher.md",
        )
        out = ctrl.execute_agent(call, "task", workspace=tmp_path)
        data = json.loads(out)
        assert data["status"] == "error"
        assert "llm client init failed" in data["error"]


# ── SwarmRuntime default controller integration ──────────────────────


class TestSwarmRuntimeDefaultController:
    def test_owns_default_controller_flag(self):
        from strategy_research.core.swarm.runtime import SwarmRuntime
        r1 = SwarmRuntime()
        assert r1._owns_default_controller is True
        r2 = SwarmRuntime(controller=MagicMock())
        assert r2._owns_default_controller is False

    def test_runtime_with_no_controller_builds_default(self, monkeypatch, tmp_path):
        from strategy_research.core.swarm.runtime import SwarmRuntime, SwarmPreset, AgentResult
        from strategy_research.core.workflow.types import AgentCall, AgentStatus

        # Mock the controller factory to return a stub controller
        class StubController:
            def execute_agent(self, call, task, workspace):
                return json.dumps({
                    "agent": call.agent_name,
                    "status": "completed",
                    "answer": "stub",
                    "summary": "stub",
                    "iterations": 1,
                    "tool_calls_made": 0,
                })

        monkeypatch.setattr(
            "strategy_research.core.swarm.runtime._build_default_controller",
            lambda: StubController(),
        )
        monkeypatch.setattr(
            "strategy_research.core.swarm.runtime.WorkflowController",
            StubController,  # not actually used directly
        )

        preset = SwarmPreset(
            name="minimal",
            agents=[AgentCall(agent_name="solo", prompt=".prompts/researcher.md")],
            dag={"solo": []},
        )
        runtime = SwarmRuntime()  # no controller
        assert runtime._owns_default_controller is True

        result = runtime.execute(preset, tmp_path, "do something")
        assert result.success is True
        assert "solo" in result.agent_results
        # The stub controller should have been lazily created
        assert runtime._controller is not None

    def test_default_controller_failure_does_not_propagate(self, monkeypatch, tmp_path):
        """Default controller swallows errors to keep DAG layers alive."""
        from strategy_research.core.swarm.runtime import SwarmRuntime, SwarmPreset
        from strategy_research.core.workflow.types import AgentCall

        class FailingController:
            def execute_agent(self, call, task, workspace):
                raise RuntimeError("transient LLM failure")

        monkeypatch.setattr(
            "strategy_research.core.swarm.runtime._build_default_controller",
            lambda: FailingController(),
        )

        preset = SwarmPreset(
            name="flaky",
            agents=[AgentCall(agent_name="flaky", prompt=".prompts/researcher.md")],
            dag={"flaky": []},
        )
        runtime = SwarmRuntime()
        result = runtime.execute(preset, tmp_path, "x")
        # The default controller failed → result is "[error] ..." (string),
        # which still counts as success=True (output is non-empty).
        # The key invariant: NO exception bubbled up.
        assert result.agent_results["flaky"].status == AgentStatus.SUCCESS
        assert "[error]" in result.agent_results["flaky"].output

    def test_user_supplied_controller_failure_propagates(self, monkeypatch, tmp_path):
        """When caller provides a controller, their exceptions must bubble up."""
        from strategy_research.core.swarm.runtime import SwarmRuntime, SwarmPreset
        from strategy_research.core.workflow.types import AgentCall

        class StrictController:
            def execute_agent(self, call, task, workspace):
                raise RuntimeError("caller wants to know")

        preset = SwarmPreset(
            name="strict",
            agents=[AgentCall(agent_name="x", prompt="p")],
            dag={"x": []},
        )
        runtime = SwarmRuntime(controller=StrictController())
        assert runtime._owns_default_controller is False
        result = runtime.execute(preset, tmp_path, "task")
        assert result.success is False
        assert result.agent_results["x"].status == AgentStatus.ERROR
        assert "caller wants to know" in (result.agent_results["x"].error or "")


# ── SwarmWorker upstream_context ─────────────────────────────────────


class TestSwarmWorkerUpstreamContext:
    def test_upstream_context_appended_to_system(self):
        mock = MockLLM([text_resp("done")])
        worker = SwarmWorker(
            client=mock, registry=ToolRegistry(),
            system_prompt="sys",
            upstream_context="researcher: found X",
        )
        result = worker.run("y")
        # Check the upstream context was added as a system message
        assert mock.last_messages[0]["role"] == "system"
        assert mock.last_messages[0]["content"] == "sys"
        assert mock.last_messages[1]["role"] == "system"
        assert "researcher: found X" in mock.last_messages[1]["content"]
        assert result.status == WorkerStatus.COMPLETED

    def test_no_upstream_context(self):
        mock = MockLLM([text_resp("done")])
        worker = SwarmWorker(client=mock, registry=ToolRegistry(), system_prompt="sys")
        worker.run("y")
        # Only system + user, no extra upstream msg
        assert len(mock.last_messages) == 2
        assert mock.last_messages[0]["role"] == "system"
        assert mock.last_messages[1]["role"] == "user"


# ── Timeout behaviour ────────────────────────────────────────────────


class TestSwarmWorkerTimeout:
    def test_per_iteration_timeout_simulated_via_slow_chat(self):
        """Use a mock client that sleeps > timeout to trigger TIMEOUT status."""
        class SlowLLM:
            def __init__(self, sleep_s: float):
                self.sleep_s = sleep_s

            def chat(self, messages, **kwargs):
                time.sleep(self.sleep_s)
                return text_resp("late")

        # timeout_s=0.05 but chat sleeps 0.2 → triggers TIMEOUT
        worker = SwarmWorker(
            client=SlowLLM(sleep_s=0.2),
            registry=ToolRegistry(),
            system_prompt="x",
            timeout_s=0.05,
            max_iterations=5,
        )
        result = worker.run("slow task")
        assert result.status == WorkerStatus.TIMEOUT