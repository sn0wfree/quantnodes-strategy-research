"""Shared pytest fixtures for CLI tests.

Extracted from duplicated ``_reset_halt`` definitions across 5 test files.
"""
from __future__ import annotations

import pytest

from strategy_research.cli.halt import clear_halt


@pytest.fixture(autouse=True)
def _reset_halt():
    """Reset HALT before and after every test."""
    clear_halt()
    yield
    clear_halt()


@pytest.fixture(autouse=True, scope="session")
def _isolate_llm_bridge(tmp_path_factory):
    """Isolate the test session from the host's ``~/.quantnodes/llm.json``.

    Without this, every test that calls ``LLMConfig.load()`` inherits the
    host's real API key (e.g. minimax), causing unintended network calls
    and breaking tests that assume "no API key -> stub mode".

    Implementation: point the existing ``STRATEGY_RESEARCH_LLM_CONFIG`` env
    var (read by ``config._resolve_bridge_path``) at a fixture JSON whose
    ``enabled`` flag is false. ``_load_bridge_dict`` then returns ``{}``,
    so the cascade falls through to code defaults and env vars only.

    Uses a session-scoped ``pytest.MonkeyPatch`` instance (the built-in
    function-scoped ``monkeypatch`` fixture cannot be used here).
    """
    fixture = tmp_path_factory.mktemp("llm_bridge") / "llm.json"
    fixture.write_text('{"llm": {"enabled": false}}')
    mp = pytest.MonkeyPatch()
    mp.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(fixture))
    yield
    mp.undo()
