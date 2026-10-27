"""入口门测试 —— chat 门保留回退；study 门已删除（阶段 4）。

阶段 3b 曾有 study 独立门（等 research preset）；3c 翻默认开；阶段 4 删门
（旧 asyncio.Queue 通道同时删除，准入唯一走 mailbox）。
"""
from __future__ import annotations

import pytest

from strategy_research.core.gateway import new_entry_enabled

# ── chat/CLI 门：默认开，保留回退 ───────────────────────


def test_chat_entry_on_by_default(monkeypatch):
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    assert new_entry_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " Off "])
def test_chat_entry_explicit_off(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY", value)
    assert new_entry_enabled() is False


# ── study 门：已删除（阶段 4）─────────────────────────────


def test_study_gate_is_gone():
    """门已删除：从 gateway 包无法导入（旧通道随之消失）。"""
    import strategy_research.core.gateway as gw

    assert not hasattr(gw, "study_entry_enabled")


def test_study_scheduler_ignores_old_gate_env(monkeypatch, tmp_path):
    """设 QNODES_NEW_ENTRY_STUDY=0 不再改变 scheduler 行为（恒走 mailbox）。"""
    import asyncio

    from strategy_research.core.study import StudyScheduler, StudyStatus, StudyStore
    from strategy_research.core.study.models import StudyRecord

    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "0")
    from strategy_research.core.gateway import reset_gateway

    reset_gateway()
    store = StudyStore(db_path=tmp_path / "s.db")
    sched = StudyScheduler(store=store)
    rec = StudyRecord(
        study_id="g1", session_id="sess-g", goal_id="g", objective="o",
        executor_type="autoresearch", workspace_path=str(tmp_path), strategy_name="s",
        execution_status=StudyStatus.QUEUED,
    )

    async def run():
        ok = await sched.submit(rec)
        await sched.shutdown()
        return ok

    try:
        assert asyncio.run(run()) is True
        assert not hasattr(sched, "_session_queues")  # 旧通道已删除
    finally:
        store.close()
        reset_gateway()
