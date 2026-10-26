"""真实 LLM 集成测试（可选）—— 显式绕过 conftest 的 LLM 隔离。

背景：tests/conftest.py 有两层 autouse 隔离，防止测试继承宿主机 key：
  1. `_isolate_llm_bridge` 把 STRATEGY_RESEARCH_LLM_CONFIG 指向空配置
  2. `_purge_llm_env` 每个测试清空 LLM_* 环境变量
所以本文件必须自己恢复真实配置与 key，否则会拿到空 config。

运行（需宿主已配 minimax key；默认跳过，须显式开启）：
    $env:QNODES_LIVE_LLM="1"; py -3.12 -m pytest tests/test_llm_live.py -m llm -v
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm

_REAL_CONFIG = Path.home() / ".quantnodes" / "llm.json"
_DOTENV = Path.home() / ".quantnodes" / ".env"


def _host_key_available() -> bool:
    if not _DOTENV.exists():
        return False
    text = _DOTENV.read_text(encoding="utf-8", errors="ignore")
    return "MINIMAX_API_KEY=" in text and len(
        [ln for ln in text.splitlines() if ln.startswith("MINIMAX_API_KEY=") and ln.split("=", 1)[1].strip()]
    ) > 0


requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1 (and `-m llm`)",
)


@pytest.fixture
def real_llm_config(monkeypatch):
    """绕过 conftest 隔离：恢复真实 llm.json 路径并注入 .env 中的 key。"""
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(_REAL_CONFIG))
    # _purge_llm_env 已清空；从 .env 里补回本次测试需要的 key
    if _DOTENV.exists():
        for line in _DOTENV.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "MINIMAX_API_KEY" and value.strip():
                monkeypatch.setenv("MINIMAX_API_KEY", value.strip())
                break

    from strategy_research.core.llm import LLMConfig

    cfg = LLMConfig.load()
    assert cfg.api_key, "host key not resolved"
    return cfg


@requires_live
def test_live_llm_node_returns_content(real_llm_config):
    from strategy_research.core.llm import OpenAICompatClient
    from strategy_research.core.runtimes.nodes.llm import make_llm_node

    client = OpenAICompatClient(real_llm_config)
    node = make_llm_node(client, system="回答不超过 20 个字。")
    out = node({"messages": [{"role": "user", "content": "用一个词回答：1+1=?"}]})
    assert isinstance(out["answer"], str)
    assert out["answer"].strip()
    assert out["messages"][-1]["role"] == "assistant"


@requires_live
def test_live_slice_end_to_end(real_llm_config):
    from strategy_research.core.gateway import SessionController
    from strategy_research.core.llm import OpenAICompatClient
    from strategy_research.core.orchestration import (
        EntryPoint,
        SourceKind,
        WorkKind,
        WorkRequest,
    )

    events: list[str] = []
    client = OpenAICompatClient(real_llm_config)
    sink = lambda kind, data: events.append(kind)  # noqa: E731
    ep = EntryPoint(sink=sink)
    controller = SessionController(ep, client=client, system="回答不超过 20 个字。", sink=sink)

    receipt = ep.submit(
        WorkRequest(
            kind=WorkKind.CHAT,
            source=SourceKind.API_CHAT,
            session_key="chat:live-test",
            payload={"text": "用一个词回答：天空是什么颜色？"},
        )
    )
    assert receipt.accepted

    [run] = controller.run_pending()
    assert run.ok, run.error
    assert run.answer.strip()
    for expected in (
        "steer.downgraded", "work.enqueued", "turn.admitted",
        "run.started", "node.started", "node.completed", "run.settled",
    ):
        assert expected in events, expected
