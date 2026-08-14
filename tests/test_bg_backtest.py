"""Tests for RunBacktestTool(background=True) — in-process pipeline
backgrounded via a thread + log progress + shared task registry."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.builtin_tools import backtest_tools as bt
from strategy_research.core.agent.builtin_tools.bg_tools import RunBgCommandTool
from strategy_research.core.agent.tools import ToolContext


def _make_step(**returns):
    """Factory: a step class whose execute returns the given dict."""

    class _Step:
        def execute(self, **kwargs):
            return json.dumps(returns, ensure_ascii=False)

    return _Step


_CFG_OK = _make_step(status="ok", cfg={"market_type": "test"}, yaml_path=None)
_PREP_OK = _make_step(status="ok")
_GATE_OK = _make_step(status="ok", report={"ready": True})


class _FakeEngineStep:
    def execute(self, **kwargs):
        return json.dumps({
            "status": "ok", "run": "run_0001",
            "metrics": {"calmar": 0.62, "sharpe": 0.4, "max_dd": -0.1},
            "factor_failures": [], "warnings": [],
        })


class _FakeSlowEngineStep(_FakeEngineStep):
    def execute(self, **kwargs):
        time.sleep(0.5)
        return super().execute(**kwargs)


@pytest.fixture
def fake_steps(monkeypatch):
    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _PREP_OK)
    monkeypatch.setattr(bt, "DataReadinessStep", _GATE_OK)
    monkeypatch.setattr(bt, "EngineRunStep", _FakeEngineStep)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "demo").mkdir(parents=True)
    return tmp_path


def _invoke_bg(tool: RunBgCommandTool, ctx, **kw) -> dict:
    return json.loads(tool.execute(ctx, **kw))


def test_background_returns_running_and_completes(ws, fake_steps):
    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    assert out["status"] == "running"
    assert out["task_id"].startswith("bg_")
    log = Path(out["log"])
    assert log.parent.exists()

    bg = RunBgCommandTool()
    # poll until done (fake steps are instant)
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done", st
    # done state carries the pipeline result
    assert st["result"]["status"] == "ok"
    assert st["result"]["run"] == "run_0001"
    assert st["result"]["metrics"]["calmar"] == 0.62
    # progress lines were written to the log
    content = log.read_text(encoding="utf-8")
    assert "[backtest] step: config load" in content
    assert "[backtest] done" in content


def test_background_slow_engine_logs_progress(ws, monkeypatch):
    """Slow engine step keeps the log advancing (heartbeat via steps)."""
    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _PREP_OK)
    monkeypatch.setattr(bt, "DataReadinessStep", _GATE_OK)
    monkeypatch.setattr(bt, "EngineRunStep", _FakeSlowEngineStep)

    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    # mid-run status must report running, not stalled
    time.sleep(0.2)
    bg = RunBgCommandTool()
    st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
    assert st["state"] == "running", st
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done"


def test_background_step_error_reported(ws, monkeypatch):
    class _FailingPrepare:
        def execute(self, **kwargs):
            return json.dumps({"status": "error", "error": "no data"})

    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _FailingPrepare)

    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    bg = RunBgCommandTool()
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done"
    assert st["result"]["status"] == "error"
    assert "no data" in st["result"]["error"]


def test_foreground_mode_unchanged(ws, fake_steps):
    """background=False (default) keeps the blocking ok result shape."""
    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx, strategy_name="demo")
    assert out["status"] == "ok"
    assert out["run"] == "run_0001"
    assert "task_id" not in out
