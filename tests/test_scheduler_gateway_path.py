"""scheduler 双路径测试（阶段 3a 引入，3c 门翻默认后更新）。

验证点：
- 门关（QNODES_NEW_ENTRY_STUDY=0）：走旧 per-session asyncio.Queue
- 门开（默认）：进 Gateway mailbox，且被 scheduler 的 mailbox 消费者取走
- 防重语义保留：重复 submit 仍被拒（active / queued / terminal 检查在上方）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from strategy_research.core.study import StudyScheduler, StudyStatus, StudyStore
from strategy_research.core.study.models import StudyRecord


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    monkeypatch.setenv("SR_STUDY_MAX_CONCURRENT", "1")
    # 3c 起两个门默认开；本文件显式设定，避免跨测试环境残留
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "0")
    from strategy_research.core.gateway import reset_gateway

    reset_gateway()
    yield
    reset_gateway()


@pytest.fixture
def store(tmp_path: Path) -> StudyStore:
    return StudyStore(db_path=tmp_path / "study.db")


def _record(study_id: str, session_id: str = "sess-1") -> StudyRecord:
    return StudyRecord(
        study_id=study_id,
        session_id=session_id,
        goal_id="g1",
        objective="o",
        executor_type="autoresearch",
        workspace_path="/tmp/ws",
        strategy_name="s1",
        execution_status=StudyStatus.QUEUED,
    )


def test_flag_off_uses_legacy_queue(store, monkeypatch, capsys):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "0")
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s1"))
        queues = dict(sched._session_queues)
        await sched.shutdown()
        return ok, queues

    ok, queues = asyncio.run(run())
    assert ok is True, f"submit returned {ok}"
    assert "sess-1" in queues, f"queues={queues}"


def test_flag_on_routes_to_gateway(store, monkeypatch):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "1")
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s2"))
        await asyncio.sleep(0.05)  # 让 mailbox 消费者取走工单
        return ok

    ok = asyncio.run(run())
    assert ok is True
    # 旧队列未被创建（改走 Gateway mailbox）
    assert "sess-1" not in sched._session_queues

    from strategy_research.core.study.scheduler import _get_gateway_singleton

    gw = _get_gateway_singleton()
    mailbox = gw.entry.mailbox_for("automation:sess-1")
    assert mailbox is not None
    # 3c：消费者立即取走（投递成功 ≠ 积压）；队列空 = 已被消费
    assert mailbox.peek_depth() == 0


def test_flag_on_duplicate_submit_rejected(store, monkeypatch):
    """防重语义在两种通道下都必须成立。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "1")
    sched = StudyScheduler(store=store)
    rec = _record("dup-1")

    async def run():
        first = await sched.submit(rec)
        second = await sched.submit(rec)
        await asyncio.sleep(0.05)
        return first, second

    first, second = asyncio.run(run())
    assert first is True
    assert second is False

    from strategy_research.core.study.scheduler import _get_gateway_singleton

    gw = _get_gateway_singleton()
    # 工单只有一条（第二次被拒），且已被消费者取走
    assert gw.entry.mailbox_for("automation:sess-1").peek_depth() == 0


def test_flag_on_terminal_study_rejected(store, monkeypatch):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "1")
    sched = StudyScheduler(store=store)
    created = store.create_study(
        owner_session_id="sess-1",
        goal_id="g1",
        objective="o",
        workspace_path="/tmp/ws",
        strategy_name="s1",
    )
    study_id = created.study_id if hasattr(created, "study_id") else created
    store.update_execution_status(study_id, StudyStatus.COMPLETE)

    async def run():
        return await sched.submit(_record(study_id))

    assert asyncio.run(run()) is False
