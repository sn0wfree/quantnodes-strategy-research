"""Tests for researcher_output string handling in _rebuild_phase_outputs.

Root cause: an agent that hits max_iterations yields a plain-text
answer ("Reached max_iterations=...") instead of the expected JSON
action object. _rebuild_phase_outputs passed the string through, and
phase_engine.py crashed with AttributeError: 'str' object has no
attribute 'get'.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.study.runner import AutoresearchRunner


def _make_runner() -> AutoresearchRunner:
    """Minimal runner with stubbed __init__ (no DB / workspace)."""
    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    return runner


class TestRebuildResearcherOutput(unittest.TestCase):
    def setUp(self):
        self.runner = _make_runner()
        # Minimal fake graph — _rebuild_phase_outputs only reads
        # agent_outputs keys, graph is unused for these fields.
        self.graph = MagicMock()

    def _rebuild(self, researcher):
        return self.runner._rebuild_phase_outputs(
            {"researcher": researcher, "backtest": {}, "decide": {}},
            self.graph,
        )

    def test_max_iter_string_falls_back_to_empty_dict(self):
        """The exact crash payload from the production traceback."""
        result = self._rebuild(
            "Reached max_iterations=20 without a final answer.",
        )
        self.assertEqual(result["researcher_output"], {})

    def test_json_string_is_parsed(self):
        result = self._rebuild(
            '{"action": "optimize_param", "hypothesis": "top_n 10->5"}',
        )
        self.assertEqual(result["researcher_output"]["action"], "optimize_param")
        self.assertEqual(
            result["researcher_output"]["hypothesis"], "top_n 10->5",
        )

    def test_non_dict_json_falls_back_to_empty_dict(self):
        result = self._rebuild("[1, 2, 3]")
        self.assertEqual(result["researcher_output"], {})

    def test_dict_passes_through(self):
        payload = {"action": "blocker", "hypothesis": "rate limited"}
        result = self._rebuild(payload)
        self.assertIs(result["researcher_output"], payload)

    def test_none_falls_back_to_empty_dict(self):
        result = self._rebuild(None)
        self.assertEqual(result["researcher_output"], {})


if __name__ == "__main__":
    unittest.main()
