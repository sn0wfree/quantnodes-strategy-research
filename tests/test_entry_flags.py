"""双门灰度测试 —— chat/CLI 默认开，study 默认关（B1 前）。

阶段 3b 的关键设计：两个入口用**不同的**门。
- new_entry_enabled(): chat/CLI，默认开（已验证）
- study_entry_enabled(): scheduler/study，默认关（等 research preset / B1）

原因：study 执行需要 GraphEngine 跑阶段图，而当前只有 llm 节点。
切过去会让 study 无法真正执行——所以必须等 B1。
"""
from __future__ import annotations

import pytest

from strategy_research.core.gateway import new_entry_enabled, study_entry_enabled


# ── chat/CLI 门：默认开 ─────────────────────────────────


def test_chat_entry_on_by_default(monkeypatch):
    monkeypatch.delenv("QNODES_NEW_ENTRY", raising=False)
    assert new_entry_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " Off "])
def test_chat_entry_explicit_off(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY", value)
    assert new_entry_enabled() is False


# ── study 门：默认关 ───────────────────────────────────


def test_study_entry_off_by_default(monkeypatch):
    monkeypatch.delenv("QNODES_NEW_ENTRY_STUDY", raising=False)
    assert study_entry_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
def test_study_entry_explicit_on(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", value)
    assert study_entry_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_study_entry_explicit_off(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", value)
    assert study_entry_enabled() is False


# ── 两门独立 ───────────────────────────────────────────


def test_doors_are_independent(monkeypatch):
    """关 chat 门不影响 study 门，反之亦然。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY", "0")
    monkeypatch.delenv("QNODES_NEW_ENTRY_STUDY", raising=False)
    assert new_entry_enabled() is False
    assert study_entry_enabled() is False

    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "1")
    assert new_entry_enabled() is True
    assert study_entry_enabled() is True

    monkeypatch.setenv("QNODES_NEW_ENTRY", "0")
    assert study_entry_enabled() is True  # study 门不受 chat 门影响
