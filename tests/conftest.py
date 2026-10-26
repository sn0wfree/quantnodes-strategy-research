"""Shared pytest fixtures for CLI tests.

Extracted from duplicated ``_reset_halt`` definitions across 5 test files.
"""
from __future__ import annotations

import os
from pathlib import Path

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


# Archived dead tests (tests/attic/) must never be collected — they
# reference modules removed in the P4/P8/Phase-A cleanups. See
# tests/attic/README.md.
collect_ignore_glob = ["attic/*"]


# ── Live LLM fixtures（opt-in：QNODES_LIVE_LLM=1 时才可用） ──────────────
# 供 test_llm_live.py / test_react_live.py 使用。这两个文件标记 llm 且
# 自带「未显式开启即 skip」的门，所以这里的绕过逻辑不会影响普通单测。
# bypass 手法与上面的 autouse 隔离相反：恢复真实 llm.json 路径、回填
# ~/.quantnodes/.env 中的 key。

def _host_key_env() -> dict[str, str]:
    env: dict[str, str] = {}
    dotenv = Path.home() / ".quantnodes" / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if value.strip():
                env[name.strip()] = value.strip()
    return env


@pytest.fixture(scope="session")
def real_llm_config():
    """真实 LLMConfig（bypass 隔离）。key 不在 → skip。"""
    if os.environ.get("QNODES_LIVE_LLM") != "1":
        pytest.skip("live LLM is opt-in: set QNODES_LIVE_LLM=1")
    real_json = Path.home() / ".quantnodes" / "llm.json"
    if not real_json.exists():
        pytest.skip(f"no {real_json}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(real_json))
    for name, value in _host_key_env().items():
        monkeypatch.setenv(name, value)

    from strategy_research.core.llm import LLMConfig

    cfg = LLMConfig.load()
    if not cfg.api_key:
        monkeypatch.undo()
        pytest.skip("host llm.json/.env 未解析出 api_key")
    yield cfg
    monkeypatch.undo()


@pytest.fixture(scope="session")
def live_client(real_llm_config):
    from strategy_research.core.llm import OpenAICompatClient

    return OpenAICompatClient(real_llm_config)


# ── Lane markers (docs/test-marker-lanes-design.md) ──────────────
# Centralized file-name whitelist: every test NOT listed here stays in
# the PR gate (default-safe — missing an entry only makes PRs slower;
# a wrong entry silently drops coverage, so review whitelist changes).
# Markers are registered in pyproject.toml [tool.pytest.ini_options].

_LANE_LLM_FILES = frozenset({
    # real LLM / real network (see doc §3)
    "test_chat_real_llm.py",
    "test_web_search.py",           # live DDGS queries (skipif HAS_NETWORK)
    "test_llm_live.py",             # opt-in live LLM (QNODES_LIVE_LLM=1)
    "test_react_live.py",           # opt-in live react tool-loop
    "test_entry_migration_live.py", # opt-in live gateway/entry migration
})

_LANE_E2E_FILES = frozenset({
    # cross-process / browser / full-stack; only the heavy ones —
    # fast e2e-named files (0.01–0.5s, mock-backed) stay in the PR gate
    # because excluding them buys no time and costs coverage.
    "test_chat_e2e_playwright.py",  # browser-driven
    "test_goal_e2e.py",
    "test_sse_e2e.py",
    "test_study_e2e_api.py",
    "test_study_e2e_integration.py",
    "test_integration.py",
    "test_p3_integration.py",
})

_LANE_SLOW_FILES = frozenset({
    # >=5s wall time in .shard-timings.json (measured 2026-10-08)
    "test_dead_code_no_imports.py",   # 75.2s — repo-wide AST scan
    "test_bg_proc.py",                # 19.3s
    "test_backtest.py",               # 16.9s
    "test_decide_wired.py",           # 16.3s
    "test_cli_init.py",               # 9.4s
    "test_llm_retry.py",              # 8.4s
    "test_study_api.py",              # 8.4s
    "test_mcp_data_tools.py",         # 7.2s
    "test_api.py",                    # 6.2s
    "test_backtest_metrics_contract.py",  # 6.2s
    "test_config_runner_import.py",   # 6.0s
    "test_data_readiness.py",         # 5.8s
    "test_security_hardening.py",     # 5.8s
    "test_load_ohlcv.py",             # 5.7s
    "test_study_actions.py",          # 5.3s
    "test_hypothesis_ops.py",         # 5.2s
    # added after the first incremental timings fill (2026-10-08):
    "test_alpha_zoo.py",              # 418.3s - repo slowest by far
    "test_swarm_presets_30.py",       # 22.9s
    "test_tools_integration.py",      # 16.5s
    "test_tool_retry.py",             # 8.1s
    "test_system_api.py",             # 7.3s
    "test_all_tools.py",              # 5.4s
    "test_session_triggers.py",       # perf-baseline bulk insert ~10s on Windows
    # not listed (covered by other lanes): test_chat_real_llm (llm),
    # test_integration + test_goal_e2e (e2e)
})


def pytest_collection_modifyitems(items):
    """Tag collected tests with their lane (llm / e2e / slow)."""
    from pathlib import Path

    for item in items:
        fname = Path(str(item.fspath)).name
        if fname in _LANE_LLM_FILES:
            item.add_marker(pytest.mark.llm)
        if fname in _LANE_E2E_FILES:
            item.add_marker(pytest.mark.e2e)
        if fname in _LANE_SLOW_FILES:
            item.add_marker(pytest.mark.slow)
