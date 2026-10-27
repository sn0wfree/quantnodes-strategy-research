"""双门灰度测试 —— chat/CLI 默认开；study 门 3c 起也默认开。

阶段 3b 曾把 study 门默认关（resume preset 能力未就绪）；B1-a/B1-b 交付后
阶段 3c 翻默认为开，撤销方式 = QNODES_NEW_ENTRY_STUDY=0（旧路径保留到阶段 4）。
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


# ── study 门：3c 起默认开 ──────────────────────────────


def test_study_entry_on_by_default(monkeypatch):
    monkeypatch.delenv("QNODES_NEW_ENTRY_STUDY", raising=False)
    assert study_entry_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_study_entry_explicit_off(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", value)
    assert study_entry_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
def test_study_entry_explicit_on(monkeypatch, value):
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", value)
    assert study_entry_enabled() is True


# ── 两门独立 ───────────────────────────────────────────


def test_doors_are_independent(monkeypatch):
    """关 chat 门不影响 study 门，反之亦然。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY", "0")
    monkeypatch.delenv("QNODES_NEW_ENTRY_STUDY", raising=False)
    assert new_entry_enabled() is False
    assert study_entry_enabled() is True  # 独立：study 门不受 chat 门影响

    monkeypatch.setenv("QNODES_NEW_ENTRY", "1")
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "0")
    assert new_entry_enabled() is True
    assert study_entry_enabled() is False
