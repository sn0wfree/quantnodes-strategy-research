"""阶段 3a：scheduler 灰度路径测试（flag 开 → 走统一入口）。

验证点：
- flag 开：submit 不落旧 per-session asyncio.Queue，而是进 Gateway mailbox
- 防重语义保留：重复 submit 仍被拒（active / queued / terminal 检查在上方）
- flag 关：旧路径照旧（由既有 test_study_scheduler.py 覆盖）
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
    # 默认关闭灰度：每个测试显式设定，避免跨测试环境残留
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    # 清 Gateway 单例：模块级缓存的 mailbox 会跨测试残留队列
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
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s1"))
        return ok, dict(sched._session_queues)

    ok, queues = asyncio.run(run())
    print("DIAG ok=", ok, "queues=", queues)
    assert ok is True, f"submit returned {ok}"
    assert "sess-1" in queues, f"queues={queues}"


def test_flag_on_routes_to_gateway(store, monkeypatch):
    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s2"))
        return ok

    ok = asyncio.run(run())
    assert ok is True
    # 旧队列未被创建（改走 Gateway mailbox）
    assert "sess-1" not in sched._session_queues

    from strategy_research.core.study.scheduler import _get_gateway_singleton

    gw = _get_gateway_singleton()
    mailbox = gw.entry.mailbox_for("automation:sess-1")
    assert mailbox is not None
    assert mailbox.peek_depth() == 1


def test_flag_on_duplicate_submit_rejected(store, monkeypatch):
    """防重语义在两种通道下都必须成立。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
    sched = StudyScheduler(store=store)
    rec = _record("dup-1")

    async def run():
        first = await sched.submit(rec)
        second = await sched.submit(rec)
        return first, second

    first, second = asyncio.run(run())
    assert first is True
    assert second is False

    from strategy_research.core.study.scheduler import _get_gateway_singleton

    gw = _get_gateway_singleton()
    assert gw.entry.mailbox_for("automation:sess-1").peek_depth() == 1


def test_flag_on_terminal_study_rejected(store, monkeypatch):
    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
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
