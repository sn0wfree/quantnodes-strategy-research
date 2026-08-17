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


@pytest.fixture(autouse=True)
def _isolate_workspace_env(tmp_path):
    """Snapshot/restore workspace env vars around every test.

    Guards against tests that mutate ``SR_WORKSPACE_PATH`` /
    ``SR_SESSIONS_DB`` via raw ``os.environ`` and forget to restore them —
    a leaked value redirects every later test's session DB to one shared
    file, producing cascade ``database is locked`` failures (the suite is
    order-sensitive otherwise).
    
    Also sets ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` to a temporary path
    to prevent tests from writing to the production database.
    """
    import os

    saved = {
        k: os.environ.get(k)
        for k in (
            "SR_WORKSPACE_PATH", "SR_SESSIONS_DB", "HYPOTHESIS_USE_SQLITE",
            "STATIC_DIR", "QUANTNODES_RESEARCH_GOAL_DB_PATH",
        )
    }
    
    # Set goal DB to a temporary path to isolate tests from production
    test_goal_db = tmp_path / "test_goals.db"
    os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(test_goal_db)
    
    yield
    
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True, scope="session")
def _default_session_workspace(tmp_path_factory):
    """Default ``SR_WORKSPACE_PATH`` to a session temp dir.

    Code paths that fall back to the current working directory (TUI/CLI
    chat flow, memory manager) otherwise write to the repo-root
    ``.quantnodes_strategy_research_session.db``, shared across the whole
    test session — cross-test message pollution produced order-dependent
    failures (e.g. ``test_tui_event_routing`` expecting an empty
    history). Tests that need a specific workspace override it per-test;
    ``_isolate_workspace_env`` restores this default afterwards.
    """
    workspace = tmp_path_factory.mktemp("session_workspace")
    mp = pytest.MonkeyPatch()
    mp.setenv("SR_WORKSPACE_PATH", str(workspace))
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Undo process-wide logging side effects of ``create_app()``.

    ``api.app.create_app`` calls ``logging.basicConfig`` and sets the
    ``strategy_research`` namespace level (default INFO), which silently
    filters DEBUG logs for every later test — e.g. projector tests that
    ``assertLogs(level="DEBUG")`` fail only when run after a test that
    built the app.
    """
    import logging

    root = logging.getLogger()
    sr_logger = logging.getLogger("strategy_research")
    saved_root_level = root.level
    saved_sr_level = sr_logger.level
    yield
    root.setLevel(saved_root_level)
    sr_logger.setLevel(saved_sr_level)

    # Reset the main-thread asyncio loop binding. Sync tests that touch
    # asyncio (e.g. sse_buffer push) call ``asyncio.get_event_loop()``,
    # which can return a closed loop left behind by an earlier test;
    # ``asyncio.Event.set()`` then silently fails. Detaching lets
    # get_event_loop() create a fresh loop (pytest-asyncio re-sets its
    # own loop for async tests).
    import asyncio

    try:
        asyncio.set_event_loop(None)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _close_session_services():
    """Release process-level singletons holding SQLite connections after
    every test.

    ``chat._session_service_cache`` holds one SessionService per DB path and
    ``MemoryManagerFactory`` keeps a process-singleton SQLiteStore for the
    suite's lifetime. A failed/left-open write transaction on either keeps
    the DB write-locked, so a later test's schema migration
    (``web_session._ensure_schema``) fails with ``database is locked``.
    """
    yield
    import strategy_research.api.routers.chat as _chat_mod

    cache = _chat_mod._session_service_cache
    for svc in cache.values():
        store = getattr(svc, "store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
    cache.clear()

    # Close the process-singleton MemoryManager (its SQLiteStore keeps a
    # connection that may hold an uncommitted transaction).
    from strategy_research.core.agent.memory_manager import MemoryManagerFactory

    try:
        MemoryManagerFactory.reset()
    except Exception:
        pass

    # Drop thread-local web_session connections on the main thread.
    import strategy_research.api.routers.web_session as _ws_mod

    tl = getattr(_ws_mod, "_db_thread_local", None)
    if tl is not None:
        conns = getattr(tl, "conns", None)
        if conns:
            for conn in conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            conns.clear()
