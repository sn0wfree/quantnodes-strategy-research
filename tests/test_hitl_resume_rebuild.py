"""PR-A regression: HITL resume rebuild path.

- ``_wait_for_approval`` must report the actual decision
  (approved/rejected/timeout), not collapse rejection into timeout.
- ``AutoresearchRunner._resume_round_langgraph`` must forward a real
  LangGraphProfile so the rebuilt graph contains the gate node the
  checkpoint stopped at (audit B4).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _bare_runner(**attrs):
    """Build an AutoresearchRunner without running its heavy __init__."""
    from strategy_research.core.study.runner import AutoresearchRunner

    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    for key, value in attrs.items():
        setattr(runner, key, value)
    return runner


# ── _wait_for_approval ──────────────────────────────────────

@pytest.mark.asyncio
@patch("strategy_research.core.study.runner.time.time")
async def test_wait_for_approval_returns_approved(mock_time):
    from types import SimpleNamespace

    mock_time.side_effect = lambda: 0.0
    intr = SimpleNamespace(status="approved")
    store = MagicMock()
    store.get_interrupt_for_round.return_value = intr
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=10)
    assert result == "approved"


@pytest.mark.asyncio
@patch("strategy_research.core.study.runner.time.time")
async def test_wait_for_approval_returns_rejected(mock_time):
    """Rejection must be distinguishable from timeout (B2 flip side)."""
    from types import SimpleNamespace

    mock_time.side_effect = lambda: 0.0
    intr = SimpleNamespace(status="rejected")
    store = MagicMock()
    store.get_interrupt_for_round.return_value = intr
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=10)
    assert result == "rejected"


@pytest.mark.asyncio
async def test_wait_for_approval_times_out(monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    import strategy_research.core.study.runner as runner_mod

    clock = {"now": 0.0}
    monkeypatch.setattr(runner_mod.time, "time", lambda: clock["now"])

    def _advance(_sid, _rn):
        clock["now"] += 999.0  # each poll consumes the whole budget
        return None

    store = MagicMock()
    store.get_interrupt_for_round.side_effect = _advance
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=600)
    assert result == "timeout"


# ── _resume_round_langgraph forwards profile ─────────────────

def test_resume_round_langgraph_forwards_profile():
    from strategy_research.core.study.langgraph_engine import (
        LangGraphProfile,
    )

    captured = {}

    def fake_resume(**kwargs):
        captured.update(kwargs)
        return {"verdict": "keep"}

    study = MagicMock()
    study.engine = "langgraph"
    runner = _bare_runner(_get_study=lambda: study, study_store=MagicMock())

    with patch(
        "strategy_research.core.study.langgraph_engine.resume_round_langgraph",
        side_effect=fake_resume,
    ), patch(
        "strategy_research.core.study.langgraph_engine.get_profile",
        return_value=LangGraphProfile.langgraph(),
    ) as mock_get_profile:
        runner._resume_round_langgraph(
            MagicMock(), "demo", {}, MagicMock(), MagicMock(),
            session="sess", sid="study-x", round_num=3,
            directive_text=None,
        )

    mock_get_profile.assert_called_once_with("langgraph")
    assert captured.get("profile") is not None, (
        "profile must be forwarded — without it the rebuilt graph drops "
        "the HITL gate node"
    )
    assert captured["round_num"] == 3
