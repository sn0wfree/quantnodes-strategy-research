"""Tests for rules.py — HardThresholdRule for strategy acceptance."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.strategy_acceptance.rules import (
    HardThresholdRule,
    RuleResult,
)


@dataclass(frozen=True)
class FakeConfig:
    hard_calmar_min: float = 0.5
    hard_sharpe_min: float = 0.5
    hard_max_dd_min: float = -0.3
    hard_ann_return_min: float = 0.0
    hard_trades_min: int = 10
    require_all_hard: bool = True


class TestRuleResult(unittest.TestCase):

    def test_defaults(self) -> None:
        r = RuleResult(passed=True)
        self.assertTrue(r.passed)
        self.assertEqual(r.detail, {})
        self.assertEqual(r.notes, "")

    def test_with_detail(self) -> None:
        r = RuleResult(passed=False, detail={"sharpe": False}, notes="failed")
        self.assertFalse(r.passed)
        self.assertEqual(r.detail, {"sharpe": False})
        self.assertEqual(r.notes, "failed")


class TestHardThresholdRule(unittest.TestCase):

    def setUp(self) -> None:
        self.rule = HardThresholdRule()

    def test_all_pass(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 1.0, "max_dd": -0.1, "ann_return": 0.2, "trades": 50}
        cfg = FakeConfig()
        result = self.rule.check(metrics, cfg)
        self.assertTrue(result.passed)
        self.assertTrue(all(result.detail.values()))

    def test_all_fail(self) -> None:
        metrics = {"calmar": 0.1, "sharpe": 0.1, "max_dd": -0.5, "ann_return": 0.0, "trades": 1}
        cfg = FakeConfig(hard_ann_return_min=0.1)
        result = self.rule.check(metrics, cfg)
        self.assertFalse(result.passed)
        self.assertFalse(result.detail["calmar"])
        self.assertFalse(result.detail["sharpe"])
        self.assertFalse(result.detail["max_dd"])
        self.assertFalse(result.detail["ann_return"])
        self.assertFalse(result.detail["trades"])

    def test_sharpe_fails(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 0.1, "max_dd": -0.1, "ann_return": 0.2, "trades": 50}
        cfg = FakeConfig()
        result = self.rule.check(metrics, cfg)
        self.assertFalse(result.passed)
        self.assertFalse(result.detail["sharpe"])
        self.assertTrue(result.detail["calmar"])

    def test_require_any_mode(self) -> None:
        metrics = {"calmar": 0.1, "sharpe": 0.1, "max_dd": -0.5, "ann_return": 0.0, "trades": 50}
        cfg = FakeConfig(require_all_hard=False)
        result = self.rule.check(metrics, cfg)
        self.assertTrue(result.passed)
        self.assertTrue(result.detail["trades"])

    def test_require_any_all_fail(self) -> None:
        metrics = {"calmar": 0.1, "sharpe": 0.1, "max_dd": -0.5, "ann_return": 0.0, "trades": 1}
        cfg = FakeConfig(require_all_hard=False, hard_ann_return_min=0.1)
        result = self.rule.check(metrics, cfg)
        self.assertFalse(result.passed)

    def test_ann_return_disabled(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 1.0, "max_dd": -0.1, "ann_return": -0.5, "trades": 50}
        cfg = FakeConfig(hard_ann_return_min=0.0)
        result = self.rule.check(metrics, cfg)
        # ann_return threshold is 0.0 → disabled → always passes
        self.assertTrue(result.detail["ann_return"])

    def test_ann_return_enabled(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 1.0, "max_dd": -0.1, "ann_return": -0.5, "trades": 50}
        cfg = FakeConfig(hard_ann_return_min=0.1)
        result = self.rule.check(metrics, cfg)
        self.assertFalse(result.detail["ann_return"])

    def test_max_dd_negative_comparison(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 1.0, "max_dd": -0.5, "ann_return": 0.2, "trades": 50}
        cfg = FakeConfig(hard_max_dd_min=-0.3)
        result = self.rule.check(metrics, cfg)
        # -0.5 < -0.3 → fails
        self.assertFalse(result.detail["max_dd"])

    def test_missing_metrics_default_to_zero(self) -> None:
        cfg = FakeConfig()
        result = self.rule.check({}, cfg)
        self.assertFalse(result.passed)

    def test_notes_format_all_mode(self) -> None:
        metrics = {"calmar": 0.1, "sharpe": 1.0, "max_dd": -0.1, "ann_return": 0.2, "trades": 50}
        cfg = FakeConfig()
        result = self.rule.check(metrics, cfg)
        self.assertIn("calmar", result.notes)

    def test_notes_format_any_mode(self) -> None:
        metrics = {"calmar": 0.1, "sharpe": 0.1, "max_dd": -0.5, "ann_return": 0.0, "trades": 50}
        cfg = FakeConfig(require_all_hard=False)
        result = self.rule.check(metrics, cfg)
        self.assertIn("trades", result.notes)

    def test_name_is_hard_threshold(self) -> None:
        self.assertEqual(self.rule.name, "hard_threshold")


if __name__ == "__main__":
    unittest.main()
