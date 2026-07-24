"""Entry-dispatch tests: bare / REPL-only flags → REPL, else argparse."""

from __future__ import annotations

from unittest import mock

import pytest

import strategy_research.cli.__main__ as entry
from strategy_research.cli.__main__ import _should_enter_repl, main


# ─── Detection rule ──────────────────────────────────────────────────


class TestShouldEnterRepl:
    def test_empty_argv_returns_true(self):
        assert _should_enter_repl([]) is True

    def test_banner_only_returns_true(self):
        assert _should_enter_repl(["--banner"]) is True

    def test_subcommand_returns_false(self):
        assert _should_enter_repl(["status"]) is False

    def test_help_returns_false(self):
        assert _should_enter_repl(["--help"]) is False

    def test_subcommand_with_banner_returns_false(self):
        # Mixed → argparse (banner doesn't make sense alongside subcommand)
        assert _should_enter_repl(["status", "--banner"]) is False

    def test_llm_list_profiles_returns_false(self):
        assert _should_enter_repl(["--llm-list-profiles"]) is False

    def test_unknown_flag_returns_false(self):
        assert _should_enter_repl(["--unknown"]) is False

    def test_tuple_argv_works(self):
        # argv is sequence-of-strings; tuple should also work
        assert _should_enter_repl(()) is True
        assert _should_enter_repl(("status",)) is False


# ─── main() routing ──────────────────────────────────────────────────


@pytest.fixture
def patched_argv(monkeypatch):
    """Default ``sys.argv`` for tests; do not auto-enter REPL."""
    return monkeypatch.setattr("sys.argv", ["strategy-research"])


class TestMainRouting:
    def test_empty_argv_calls_repl(self, monkeypatch, patched_argv):
        with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
            rc = main([])
        m.assert_called_once_with([])
        assert rc == 0

    def test_banner_only_forwards_to_repl_with_flag(
        self, monkeypatch, patched_argv
    ):
        with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
            rc = main(["--banner"])
        m.assert_called_once_with(["--banner"])
        assert rc == 0

    def test_subcommand_calls_argparse(self, monkeypatch, patched_argv):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["status"])
        m.assert_called_once()
        assert rc == 0

    def test_help_calls_argparse(self, monkeypatch, patched_argv):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["--help"])
        m.assert_called_once()
        assert rc == 0

    def test_llm_list_profiles_calls_argparse(self, monkeypatch, patched_argv):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["--llm-list-profiles"])
        m.assert_called_once()
        assert rc == 0

    def test_unknown_flag_calls_argparse(self, monkeypatch, patched_argv):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["--some-flag"])
        m.assert_called_once()
        assert rc == 0

    def test_main_propagates_repl_return_code(
        self, monkeypatch, patched_argv
    ):
        with mock.patch.object(entry, "_interactive_main", return_value=2):
            rc = main([])
        assert rc == 2

    def test_main_propagates_argparse_return_code(
        self, monkeypatch, patched_argv
    ):
        with mock.patch.object(entry, "_cli_main", return_value=1):
            rc = main(["status"])
        assert rc == 1

    def test_main_none_argv_uses_sys_argv(
        self, monkeypatch, patched_argv
    ):
        # patched_argv = ["strategy-research"] → empty sys.argv[1:] → REPL
        with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
            rc = main()  # None → use sys.argv[1:]
        m.assert_called_once_with([])

    def test_argparse_path_restores_sys_argv(self, monkeypatch):
        import sys as _sys
        original = _sys.argv
        with mock.patch.object(entry, "_cli_main", return_value=0):
            main(["status", "--strategy", "my_strat"])
        # After the call, sys.argv should be restored to its pre-stub state
        assert _sys.argv == original

    def test_repl_path_does_not_touch_sys_argv(self, monkeypatch):
        import sys as _sys
        original = _sys.argv
        with mock.patch.object(entry, "_interactive_main", return_value=0):
            main(["--banner"])
        assert _sys.argv == original


# ─── Routing sanity vs real imports ──────────────────────────────────


class TestRealImports:
    def test_repl_main_importable(self):
        from strategy_research.cli.interactive.main import main as repl
        assert callable(repl)

    def test_cli_main_importable(self):
        from strategy_research.cli import main as cli
        assert callable(cli)

    def test_dispatcher_module_entries(self):
        # The console_script target must exist and be callable.
        from strategy_research.cli.__main__ import main as m
        assert callable(m)
