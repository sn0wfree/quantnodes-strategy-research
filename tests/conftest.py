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


@pytest.fixture(autouse=True)
def _purge_llm_env(monkeypatch):
    """Strip host LLM credentials before every test.

    ``LLMConfig.load()`` calls ``_try_load_dotenv()`` which re-injects keys
    from ``~/.quantnodes/.env`` into ``os.environ`` after a test's own
    ``monkeypatch.delenv`` has cleared them — so the very first ``load()``
    would see a clean env, but the second (e.g. inside ``should_use_real_llm``)
    would see the dotenv-restored ``LLM_API_KEY`` and take the real-LLM path.

    This runs per-test (not session) so it re-cleans right before each test,
    and is order-independent of any test-local monkeypatch calls.

    ``_try_load_dotenv`` is neutralized entirely so ``LLMConfig.load()`` can
    never pull host keys from ``~/.quantnodes/.env`` mid-test (tests that
    genuinely need a key load it explicitly, e.g. ``test_chat_real_llm``).
    """
    for var in (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "NVIDIA_API_KEY",
        "MINIMAX_API_KEY",
        "DEEPSEEK_API_KEY",
        "ZHIPU_API_KEY",
        "QIANFAN_API_KEY",
        "MOONSHOT_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    import strategy_research.core.llm.config as _cfg
    monkeypatch.setattr(_cfg, "_try_load_dotenv", lambda: None)


@pytest.fixture(autouse=True)
def _purge_hypothesis_sqlite_env(monkeypatch):
    """Keep tests in JSON mode unless they explicitly opt into SQLite.

    ``create_app`` sets ``HYPOTHESIS_USE_SQLITE`` (v2 design §14.2 default-on),
    and some test modules call ``create_app()`` at import time — without this,
    every ``HypothesisRegistry()`` would silently switch to the host SQLite DB
    (~/.quantnodes-research/hypotheses.db) for the rest of the session.
    """
    monkeypatch.delenv("HYPOTHESIS_USE_SQLITE", raising=False)
