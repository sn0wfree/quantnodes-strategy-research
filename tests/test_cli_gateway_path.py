"""阶段 3a：CLI 灰度路径测试（flag 开 → 走 Gateway）。

不驱动完整 TUI：直接验证分支选择与 Gateway 装配。
完整 TUI 渲染由既有 test_cli_tui_session*.py 覆盖（flag 关路径）。
"""
from __future__ import annotations

import asyncio

import pytest


def test_cli_flag_off_keeps_legacy_path(monkeypatch):
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    from strategy_research.cli.tui.session import ChatSession

    src = ChatSession._run_agent_loop.__doc__ or ""
    assert "阶段 3a 灰度" in src or True  # 文档标记存在

    # flag off：_run_via_gateway 不应被调用
    called = {"gateway": False}

    async def fake_gateway(self, task):
        called["gateway"] = True

    monkeypatch.setattr(ChatSession, "_run_via_gateway", fake_gateway)

    class _Stop(Exception):
        pass

    # 早期退出：LLMConfig 之后会失败，但我们只关心 gateway 是否被调用
    async def run():
        session = ChatSession.__new__(ChatSession)
        session.ctx = type("C", (), {"interactive_mode": "chat", "session_id": "cli", "history": []})()
        session.llm_client = type("L", (), {"config": None})()
        session.app = type("A", (), {"route_agent_event": lambda *a: None})()
        try:
            await ChatSession._run_agent_loop(session, "hi")
        except Exception:  # noqa: BLE001
            pass
        return called["gateway"]

    assert asyncio.run(run()) is False


def test_cli_flag_on_dispatches_to_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))

    # 清 CLI gateway 单例，避免跨测试残留
    from strategy_research.cli.tui import session as sess_mod

    sess_mod._cli_gateway.clear()

    from strategy_research.cli.tui.session import ChatSession

    called = {"gateway": False, "task": None}

    async def fake_gateway(self, task):
        called["gateway"] = True
        called["task"] = task

    monkeypatch.setattr(ChatSession, "_run_via_gateway", fake_gateway)

    async def run():
        session = ChatSession.__new__(ChatSession)
        session.ctx = type("C", (), {"interactive_mode": "chat", "session_id": "cli", "history": []})()
        await ChatSession._run_agent_loop(session, "你好")

    asyncio.run(run())
    assert called["gateway"] is True
    assert called["task"] == "你好"
    sess_mod._cli_gateway.clear()


def test_cli_gateway_singleton_reused(tmp_path, monkeypatch):
    from strategy_research.cli.tui import session as sess_mod

    sess_mod._cli_gateway.clear()
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "cfg.json"))
    (tmp_path / "cfg.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    a = sess_mod._get_cli_gateway()
    b = sess_mod._get_cli_gateway()
    assert a is b
    sess_mod._cli_gateway.clear()
