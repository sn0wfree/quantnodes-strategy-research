"""真实 LLM 集成测试 —— 默认跳过，显式开启后覆盖完整能力面。

背景：tests/conftest.py 有两层 autouse 隔离（配置指向空文件 + 清空 LLM 环境变量），
防止普通单测意外打真实网络。本文件必须自行恢复真实配置。

运行：
    $env:QNODES_LIVE_LLM="1"
    py -3.12 -m pytest tests/test_llm_live.py -m llm -v

覆盖：
  A. 基础对话（llm 节点）        B. 多轮上下文记忆
  C. system 指令遵循（可验证）    D. 精确答案（算术/事实，可断言）
  E. usage 统计字段               F. 中文输出质量
  G. 工具调用（真实 function calling）
  H. 流式输出
  I. 八站全链路 + 多 turn 会话
  J. 长上下文（历史积累不丢）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm

_REAL_CONFIG = Path.home() / ".quantnodes" / "llm.json"
_DOTENV = Path.home() / ".quantnodes" / ".env"

requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1 (and `-m llm`)",
)


# ── 夹具：绕过 conftest 隔离，恢复真实配置 ──────────────────


@pytest.fixture(scope="session")
def real_llm_config():
    """恢复真实 llm.json 与 .env 中的 key（session 级：一次装配，多个测试复用）。"""
    if not _REAL_CONFIG.exists():
        pytest.skip(f"no {_REAL_CONFIG}")

    from unittest.mock import patch

    env_patch = {}
    if _DOTENV.exists():
        for line in _DOTENV.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if value.strip():
                env_patch[name.strip()] = value.strip()

    with patch.dict(os.environ, env_patch, clear=False):
        os.environ["STRATEGY_RESEARCH_LLM_CONFIG"] = str(_REAL_CONFIG)
        from strategy_research.core.llm import LLMConfig

        cfg = LLMConfig.load()
        if not cfg.api_key:
            pytest.skip("host llm.json/.env 未解析出 api_key")
        yield cfg


@pytest.fixture
def client(real_llm_config):
    from strategy_research.core.llm import OpenAICompatClient

    return OpenAICompatClient(real_llm_config)


# ── A. 基础对话 ──────────────────────────────────────────


@requires_live
def test_a_basic_chat_node(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="回答不超过 20 个字。")
    out = node({"messages": [{"role": "user", "content": "用一个词回答：1+1=?"}]})
    assert out["answer"].strip()
    assert out["messages"][-1]["role"] == "assistant"


# ── B. 多轮上下文 ─────────────────────────────────────────


@requires_live
def test_b_multi_turn_context_retained(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="回答不超过 15 个字。")
    state = node({"messages": [{"role": "user", "content": "记住这个数字：42"}]})
    state = node(
        {
            **state,
            "messages": state["messages"] + [{"role": "user", "content": "我刚让你记的数字是多少？"}],
        }
    )
    assert "42" in state["answer"]


# ── C. system 指令遵循（可验证行为差异） ──────────────────


@requires_live
def test_c_system_prompt_is_followed(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="你只输出 JSON，格式 {\"answer\": ...}，不要任何其他文字。")
    out = node({"messages": [{"role": "user", "content": "天空是什么颜色？"}]})
    text = out["answer"].strip()
    assert "{" in text and "}" in text, f"未遵循 JSON 指令: {text[:120]}"


# ── D. 精确答案（可断言正确性） ──────────────────────────


@requires_live
def test_d_exact_arithmetic_answer(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="只输出最终结果，不要解释。")
    out = node({"messages": [{"role": "user", "content": "17 * 23 = ?"}]})
    assert "391" in out["answer"]


# ── E. usage 统计 ─────────────────────────────────────────


@requires_live
def test_e_usage_fields_present(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client)
    out = node({"messages": [{"role": "user", "content": "说：你好"}]})
    usage = out["usage"]
    assert isinstance(usage, dict)
    assert usage, "usage 为空——provider 未返回用量"
    total = usage.get("total_tokens") or usage.get("prompt_tokens")
    assert total and total > 0


# ── F. 中文输出 ──────────────────────────────────────────


@requires_live
def test_f_chinese_output(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="用中文回答，不超过 30 字。")
    out = node({"messages": [{"role": "user", "content": "什么是夏普比率？"}]})
    answer = out["answer"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in answer), f"非中文输出: {answer[:80]}"


# ── G. 工具调用（真实 function calling） ──────────────────


@requires_live
def test_g_tool_call_is_emitted(client):
    """验证 provider 真实返回 tool_calls（react 节点的前提能力）。"""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询城市天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "城市名"}},
                    "required": ["city"],
                },
            },
        }
    ]
    response = client.chat(
        [{"role": "user", "content": "北京天气怎么样？请调用工具查询。"}],
        tools=tools,
    )
    assert response.has_tool_calls(), f"未返回 tool_calls: finish={response.finish_reason}"
    call = response.tool_calls[0]
    assert call.name == "get_weather", f"工具名不符: {call.name}"
    args = call.arguments if isinstance(call.arguments, dict) else {}
    assert "北京" in str(args) or "beijing" in str(args).lower(), f"参数缺城市: {args}"


# ── H. 流式输出 ──────────────────────────────────────────


@requires_live
def test_h_streaming_yields_incremental_chunks(client):
    chunks = []
    finish = None
    for chunk in client.stream([{"role": "user", "content": "从 1 数到 5，用逗号分隔。"}]):
        if getattr(chunk, "delta_content", None):
            chunks.append(chunk.delta_content)
        if getattr(chunk, "finish_reason", None):
            finish = chunk.finish_reason
    joined = "".join(chunks)
    assert len(chunks) > 1, "流式未分块"
    assert joined.strip()
    assert finish == "stop"


# ── I. 八站全链路 + 多 turn 会话 ──────────────────────────


@requires_live
def test_i_full_slice_two_turns(client):
    from strategy_research.core.gateway import SessionController
    from strategy_research.core.orchestration import (
        EntryPoint,
        SourceKind,
        WorkKind,
        WorkRequest,
    )

    events: list[str] = []
    sink = lambda kind, data: events.append(kind)  # noqa: E731
    ep = EntryPoint(sink=sink)
    controller = SessionController(ep, client=client, system="回答不超过 20 个字。", sink=sink)

    def submit(text: str):
        receipt = ep.submit(
            WorkRequest(
                kind=WorkKind.CHAT,
                source=SourceKind.API_CHAT,
                session_key="chat:live-multi",
                payload={"text": text},
            )
        )
        assert receipt.accepted
        return controller.run_pending()

    [run1] = submit("用一个词回答：天空是什么颜色？")
    [run2] = submit("再用一个词回答：草是什么颜色？")

    assert run1.ok and run1.answer.strip()
    assert run2.ok and run2.answer.strip()
    # turn 单调递增、run_id 派生正确（P-4）
    assert run1.turn_id.endswith("#1") and run2.turn_id.endswith("#2")
    assert run1.run_id.endswith(":r0") and run2.run_id.endswith(":r0")
    # 八站事件齐全
    for expected in (
        "steer.downgraded", "work.enqueued", "turn.admitted",
        "run.started", "node.started", "node.completed", "run.settled",
    ):
        assert expected in events, expected


# ── J. 长历史（上下文不丢） ──────────────────────────────


@requires_live
def test_j_long_history_preserved(client):
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    node = make_llm_node(client, system="回答不超过 10 个字。")
    state = {"messages": []}
    # 注入 6 轮历史，验证长历史下仍能正确引用最早的信息
    state = node(
        {
            **state,
            "messages": [{"role": "user", "content": "我的幸运数字是 7391，请记住。"}],
        }
    )
    for i in range(5):
        state = node(
            {
                **state,
                "messages": state["messages"] + [{"role": "user", "content": f"第 {i + 1} 次确认。"}],
            }
        )
    state = node(
        {
            **state,
            "messages": state["messages"] + [{"role": "user", "content": "我的幸运数字是多少？只答数字。"}],
        }
    )
    assert "7391" in state["answer"], f"长历史丢失早期信息: {state['answer'][:100]}"
