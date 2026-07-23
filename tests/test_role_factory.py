"""Phase C-1 — agent role factory + AgentLoop 真接测试。

覆盖:
- _load_role_system_prompt: 9 个 role 都加载成功
- _get_tool_whitelist: role → tool 映射正确
- should_use_real_llm: env var / api key 决策
- build_agent_loop: 构造 AgentLoop 成功 (mock LLM)
- run_agent_via_llm: 异常路径返回 JSON error
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestRolePromptLoading:
    """9 个 role 的 prompt 都能从 templates/.prompts/ 加载."""

    def test_all_10_roles_have_prompt_files(self):
        from strategy_research.core.agent.role_factory import _load_role_system_prompt
        roles = ["researcher", "data_quality", "factor_analyst", "strategist",
                 "portfolio_construction", "risk_controller", "attribution_analyst",
                 "anti_overfit_analyst", "backtest_diagnostics", "critic"]
        for role in roles:
            prompt = _load_role_system_prompt(role)
            assert len(prompt) > 50, f"{role} prompt is too short or missing"
            assert prompt.startswith("# Role:"), f"{role} prompt missing # Role: header"

    def test_unknown_role_returns_empty(self):
        from strategy_research.core.agent.role_factory import _load_role_system_prompt
        assert _load_role_system_prompt("nonexistent_role_xyz") == ""

    def test_prompt_files_match_templates(self):
        """9 个 prompt 文件名与 templates/.prompts/ 对应."""
        from strategy_research.core.agent.role_factory import _ROLE_PROMPT_FILES
        from strategy_research import _TEMPLATES_DIR
        prompts_dir = _TEMPLATES_DIR / ".prompts"
        for role, filename in _ROLE_PROMPT_FILES.items():
            assert (prompts_dir / filename).exists(), f"{filename} missing in {prompts_dir}"


class TestToolWhitelist:
    """role → tool whitelist 映射."""

    def test_strategist_has_write_tools(self):
        """strategist 必须能 write_file + run_backtest."""
        from strategy_research.core.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("strategist")
        assert "write_file" in wl
        assert "run_backtest" in wl
        assert "read_file" in wl

    def test_data_quality_minimal(self):
        """data_quality 是只读 agent, 最小工具集."""
        from strategy_research.core.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("data_quality")
        assert wl == ["read_file"]

    def test_critic_is_readonly(self):
        """critic 不能 write_file."""
        from strategy_research.core.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("critic")
        assert "write_file" not in wl
        assert "read_file" in wl

    def test_unknown_role_minimal_default(self):
        """未知 role 退到 read_file 默认最小集."""
        from strategy_research.core.agent.role_factory import _get_tool_whitelist
        assert _get_tool_whitelist("foo_bar_baz") == ["read_file"]


class TestShouldUseRealLlm:
    """should_use_real_llm() 决策."""

    def test_no_env_var_no_api_key_returns_false(self, monkeypatch):
        """没设 AUTORESEARCH_BEHAVIOR + 无 api key → 走 stub."""
        monkeypatch.delenv("AUTORESEARCH_BEHAVIOR", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("STRATEGY_RESEARCH_LLM_PROFILE", raising=False)
        from strategy_research.core.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False

    def test_env_var_set_returns_false(self, monkeypatch):
        """AUTORESEARCH_BEHAVIOR=stub → 强制走 stub, 即便有 api key."""
        monkeypatch.setenv("AUTORESEARCH_BEHAVIOR", "static")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from strategy_research.core.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False

    def test_placeholder_api_key_returns_false(self, monkeypatch):
        """api_key 是占位符 → 走 stub."""
        monkeypatch.delenv("AUTORESEARCH_BEHAVIOR", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "your-api-key-here")
        from strategy_research.core.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False


class TestBuildAgentLoop:
    """build_agent_loop() 构造 AgentLoop 实例."""

    def test_returns_agent_loop_with_prompt(self, tmp_path):
        from strategy_research.core.agent.role_factory import build_agent_loop
        from strategy_research.core.agent.loop import AgentLoop

        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        assert loop is not None
        assert isinstance(loop, AgentLoop)
        # system_prompt 应加载了 researcher.md
        assert loop.context_builder is not None
        # tool whitelist 应过滤到只读工具
        assert "compute_factor" not in [t.name for t in loop.registry._tools.values()]
        # 但应保留 read_file
        tool_names = [t.name for t in loop.registry._tools.values()]
        assert "read_file" in tool_names

    def test_strategist_has_write_tools(self, tmp_path):
        from strategy_research.core.agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role="strategist",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        assert loop is not None
        tool_names = {t.name for t in loop.registry._tools.values()}
        assert "write_file" in tool_names
        assert "run_backtest" in tool_names

    def test_unknown_role_returns_none(self, tmp_path):
        from strategy_research.core.agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role="nonexistent_role_xyz",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        # 没有 .prompts 文件 → 返回 None
        assert loop is None


class TestRunAgentViaLlm:
    """run_agent_via_llm() 异常路径."""

    def test_unknown_role_raises(self, tmp_path):
        """未知 role 应抛 RuntimeError."""
        from strategy_research.core.agent.role_factory import run_agent_via_llm
        with pytest.raises(RuntimeError, match="无法构造 AgentLoop"):
            run_agent_via_llm(
                role="nonexistent_role_xyz",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="dummy task",
            )

    def test_known_role_with_mocked_loop_returns_answer(self, tmp_path):
        """Mock AgentLoop.run() → 返回 mock answer JSON."""
        from strategy_research.core.agent import role_factory as factory
        from strategy_research.core.agent.loop import LoopResult

        mock_loop = MagicMock()
        mock_loop.run.return_value = LoopResult(
            answer='{"action": "search_external", "hypothesis": "test"}',
            iterations=2,
            tool_calls_made=1,
            finished_reason="stop",
        )

        with patch.object(factory, "build_agent_loop", return_value=mock_loop):
            result = factory.run_agent_via_llm(
                role="researcher",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="find alpha",
                previous_outputs=[{"prior": "result"}],
            )

        assert json.loads(result) == {"action": "search_external", "hypothesis": "test"}
        mock_loop.run.assert_called_once()

    def test_known_role_with_loop_error_returns_error_json(self, tmp_path):
        """AgentLoop.run() 返回 error → 应序列化成 JSON error."""
        from strategy_research.core.agent import role_factory as factory
        from strategy_research.core.agent.loop import LoopResult

        mock_loop = MagicMock()
        mock_loop.run.return_value = LoopResult(
            answer="",
            iterations=3,
            tool_calls_made=0,
            finished_reason="error",
            error="LLM 调用超时",
        )

        with patch.object(factory, "build_agent_loop", return_value=mock_loop):
            result = factory.run_agent_via_llm(
                role="factor_analyst",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="compute IC",
            )

        body = json.loads(result)
        assert "error" in body
        assert "LLM 调用超时" in body["error"]
        assert body["iterations"] == 3


class TestSpawnAgentFallback:
    """_spawn_agent() stub 路径 (无 API key 时仍可用)."""

    def test_still_returns_json_for_known_roles(self, tmp_path, monkeypatch):
        """_spawn_agent() 在无 API key 时仍返回合法 JSON (走 stub)."""
        monkeypatch.delenv("AUTORESEARCH_BEHAVIOR", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from strategy_research.cli import _spawn_agent

        for role in ["researcher", "data_quality", "factor_analyst",
                     "strategist", "portfolio_construction", "risk_controller",
                     "attribution_analyst", "anti_overfit_analyst",
                     "backtest_diagnostics", "critic"]:
            raw = _spawn_agent(
                role, tmp_path, "test_strat",
                {"total_runs": 0}, [],
            )
            data = json.loads(raw)
            assert isinstance(data, dict)
            # 应该不是 error (无 API key 时不应调真 LLM, 应直接走 stub)
            assert "error" not in data or "Unknown agent" in data.get("error", "")

    def test_env_var_force_stub(self, tmp_path, monkeypatch):
        """AUTORESEARCH_BEHAVIOR=improving → 强制 stub, round>=3 approve."""
        monkeypatch.setenv("AUTORESEARCH_BEHAVIOR", "improving")
        from strategy_research.cli import _spawn_agent

        raw = _spawn_agent(
            "critic", tmp_path, "test_strat",
            {"total_runs": 5}, [],
        )
        data = json.loads(raw)
        # round 5 with improving → should be approved
        assert data["approved"] is True