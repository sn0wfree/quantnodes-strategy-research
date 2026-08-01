"""Tests for slash_command decorator (Phase 2.3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.cli.commands.slash_decorator import (
    get_run_function,
    registered_run_aliases,
    reset_run_aliases,
    slash_command,
)
from strategy_research.cli.commands import slash_router


class TestSlashCommandDecorator(unittest.TestCase):

    def setUp(self):
        reset_run_aliases()

    def test_register_decorator(self):
        @slash_command("/test_one", description="Test one")
        def cmd_test_one(ctx=None, *args):
            return 42

        # Lookup table updated
        found = slash_router.find_exact("test_one")
        self.assertIsNotNone(found)
        self.assertEqual(found.description, "Test one")

    def test_call_via_run_alias(self):
        @slash_command("/test_two", description="Test two")
        def cmd_test_two(ctx=None, *args):
            return sum(args) if args else 0

        # Direct call works
        run_fn = get_run_function("test_two")
        self.assertIsNotNone(run_fn)
        self.assertEqual(run_fn(None, 1, 2, 3), 6)

    def test_call_via_run_alias_without_slash(self):
        @slash_command("/test_three", description="Test three")
        def cmd_test_three(ctx=None, *args):
            return "hello"

        run_fn = get_run_function("test_three")
        self.assertEqual(run_fn(), "hello")

    def test_name_must_start_with_slash(self):
        with self.assertRaises(ValueError):
            slash_command("foo", description="x")  # missing leading /

    def test_re_register_replaces(self):
        @slash_command("/test_four", description="First")
        def cmd_test_four_v1(ctx=None, *args):
            return 1

        @slash_command("/test_four", description="Second")
        def cmd_test_four_v2(ctx=None, *args):
            return 2

        # Last registration wins
        found = slash_router.find_exact("test_four")
        self.assertEqual(found.description, "Second")

    def test_match_commands_includes_new(self):
        @slash_command("/test_five", description="Test five")
        def cmd_test_five(ctx=None, *args):
            return 0

        matches = slash_router.match_commands("/test_five")
        names = [m.name for m in matches]
        self.assertIn("test_five", names)

    def test_registered_run_aliases_tracks(self):
        @slash_command("/alpha", description="Alpha")
        def cmd_alpha(ctx=None, *args):
            return 0

        @slash_command("/beta", description="Beta")
        def cmd_beta(ctx=None, *args):
            return 0

        aliases = registered_run_aliases()
        self.assertIn("run_alpha", aliases)
        self.assertIn("run_beta", aliases)

    def test_get_run_function_returns_none_for_unknown(self):
        self.assertIsNone(get_run_function("nonexistent_command"))


if __name__ == "__main__":
    unittest.main()