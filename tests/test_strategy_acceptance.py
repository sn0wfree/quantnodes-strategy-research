"""Tests for strategy_acceptance/__init__.py — decide(), config, decision."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.strategy_acceptance import (
    DEFAULT_CONFIG,
    AcceptanceConfig,
    AcceptanceDecision,
    decide,
    load_config,
)


class TestAcceptanceConfig(unittest.TestCase):

    def test_defaults(self) -> None:
        cfg = AcceptanceConfig()
        self.assertEqual(cfg.hard_calmar_min, 0.5)
        self.assertEqual(cfg.hard_sharpe_min, 0.3)
        self.assertEqual(cfg.hard_max_dd_min, -0.15)
        self.assertEqual(cfg.hard_trades_min, 30)
        self.assertEqual(cfg.hard_ann_return_min, 0.0)
        self.assertTrue(cfg.llm_enabled)
        self.assertTrue(cfg.require_all_hard)
        self.assertEqual(cfg.stagnation_patience, 10)

    def test_with_overrides(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(hard_calmar_min=0.7, hard_sharpe_min=0.5)
        self.assertEqual(cfg.hard_calmar_min, 0.7)
        self.assertEqual(cfg.hard_sharpe_min, 0.5)
        self.assertEqual(cfg.hard_trades_min, 30)

    def test_with_overrides_ignores_unknown(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(unknown_field=99)
        self.assertIs(cfg, DEFAULT_CONFIG)

    def test_with_overrides_ignores_none(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(hard_calmar_min=None)
        self.assertIs(cfg, DEFAULT_CONFIG)

    def test_frozen(self) -> None:
        cfg = AcceptanceConfig()
        with self.assertRaises(AttributeError):
            cfg.hard_calmar_min = 0.9  # type: ignore[misc]


class TestAcceptanceDecision(unittest.TestCase):

    def test_defaults(self) -> None:
        d = AcceptanceDecision(accept=True, reason="ok", hard_passed=True)
        self.assertTrue(d.accept)
        self.assertEqual(d.reason, "ok")
        self.assertIsNone(d.llm_passed)
        self.assertFalse(d.stagnation_triggered)

    def test_to_dict(self) -> None:
        d = AcceptanceDecision(
            accept=True,
            reason="passed",
            hard_passed=True,
            llm_passed=True,
            hard_detail={"calmar": True, "sharpe": True},
            llm_detail={"passed": True, "score": 0.8},
        )
        d2 = d.to_dict()
        self.assertEqual(d2["accept"], True)
        self.assertEqual(d2["reason"], "passed")
        self.assertEqual(d2["llm_detail"]["score"], 0.8)

    def test_to_dict_none_llm(self) -> None:
        d = AcceptanceDecision(accept=True, reason="ok", hard_passed=True)
        d2 = d.to_dict()
        self.assertIsNone(d2["llm_detail"])

    def test_frozen(self) -> None:
        d = AcceptanceDecision(accept=True, reason="ok", hard_passed=True)
        with self.assertRaises(AttributeError):
            d.accept = False  # type: ignore[misc]


class TestDecide(unittest.TestCase):

    def test_hard_pass_llm_skip(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 0.8, "max_dd": -0.1, "trades": 50}
        result = decide(metrics, llm_verdict=None)
        self.assertTrue(result.accept)
        self.assertTrue(result.hard_passed)
        self.assertIsNone(result.llm_passed)

    def test_hard_fail_rejected(self) -> None:
        metrics = {"calmar": 0.0, "sharpe": 0.0, "max_dd": -0.5, "trades": 1}
        result = decide(metrics, llm_verdict=None)
        self.assertFalse(result.accept)
        self.assertFalse(result.hard_passed)

    def test_hard_pass_llm_pass(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 0.8, "max_dd": -0.1, "trades": 50}
        llm = {"passed": True, "score": 0.9, "reason": "good"}
        result = decide(metrics, llm_verdict=llm)
        self.assertTrue(result.accept)
        self.assertTrue(result.hard_passed)
        self.assertTrue(result.llm_passed)

    def test_hard_pass_llm_fail(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 0.8, "max_dd": -0.1, "trades": 50}
        llm = {"passed": False, "score": 0.2, "reason": "bad"}
        result = decide(metrics, llm_verdict=llm)
        self.assertFalse(result.accept)
        self.assertTrue(result.hard_passed)
        self.assertFalse(result.llm_passed)

    def test_stagnation_override(self) -> None:
        metrics = {"calmar": 0.0, "sharpe": 0.0, "max_dd": -0.5, "trades": 1}
        cfg = AcceptanceConfig(stagnation_patience=5)
        result = decide(metrics, stagnation_count=5, cfg=cfg)
        self.assertTrue(result.accept)
        self.assertTrue(result.stagnation_triggered)

    def test_stagnation_below_patience(self) -> None:
        metrics = {"calmar": 0.0, "sharpe": 0.0, "max_dd": -0.5, "trades": 1}
        cfg = AcceptanceConfig(stagnation_patience=5)
        result = decide(metrics, stagnation_count=3, cfg=cfg)
        self.assertFalse(result.accept)
        self.assertFalse(result.stagnation_triggered)

    def test_missing_metrics_default_to_zero(self) -> None:
        result = decide({}, llm_verdict=None)
        self.assertFalse(result.accept)

    def test_require_any_mode(self) -> None:
        metrics = {"calmar": 0.0, "sharpe": 0.8, "max_dd": -0.5, "trades": 1}
        cfg = AcceptanceConfig(require_all_hard=False)
        result = decide(metrics, cfg=cfg, llm_verdict=None)
        self.assertTrue(result.accept)

    def test_llm_verdict_none_passed(self) -> None:
        metrics = {"calmar": 1.0, "sharpe": 0.8, "max_dd": -0.1, "trades": 50}
        llm = {"score": 0.8}
        result = decide(metrics, llm_verdict=llm)
        self.assertFalse(result.accept)

    def test_llm_verdict_missing_passed(self) -> None:
        d = AcceptanceDecision(accept=True, reason="ok", hard_passed=True)
        self.assertIsNone(d.llm_passed)


class TestLoadConfig(unittest.TestCase):

    def test_defaults_when_no_files(self) -> None:
        cfg = load_config()
        self.assertIsInstance(cfg, AcceptanceConfig)
        self.assertEqual(cfg.hard_calmar_min, 0.5)

    def test_cli_overrides(self) -> None:
        cfg = load_config(cli_overrides={"hard_calmar_min": 0.9})
        self.assertEqual(cfg.hard_calmar_min, 0.9)

    def test_workspace_config(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "acceptance.yaml"
            ws_path.write_text("hard_calmar_min: 0.7\n", encoding="utf-8")
            cfg = load_config(workspace_config=ws_path)
            self.assertEqual(cfg.hard_calmar_min, 0.7)

    def test_cli_overrides_workspace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "acceptance.yaml"
            ws_path.write_text("hard_calmar_min: 0.7\n", encoding="utf-8")
            cfg = load_config(
                workspace_config=ws_path,
                cli_overrides={"hard_calmar_min": 0.9},
            )
            self.assertEqual(cfg.hard_calmar_min, 0.9)

    def test_workspace_yaml_with_nested_dict(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "acceptance.yaml"
            ws_path.write_text("hard_calmar_min: 0.7\nhard_sharpe_min: 0.5\n", encoding="utf-8")
            cfg = load_config(workspace_config=ws_path)
            self.assertEqual(cfg.hard_calmar_min, 0.7)
            self.assertEqual(cfg.hard_sharpe_min, 0.5)


class TestCoerceMetrics(unittest.TestCase):

    def test_normal_values_via_decide(self) -> None:
        from strategy_research.core.strategy_acceptance import _coerce_metrics
        result = _coerce_metrics({"calmar": 1.5, "sharpe": 0.8, "max_dd": -0.2, "trades": 50})
        self.assertEqual(result["calmar"], 1.5)
        self.assertEqual(result["trades"], 50)

    def test_non_numeric_string_via_decide(self) -> None:
        from strategy_research.core.strategy_acceptance import _coerce_metrics
        result = _coerce_metrics({"calmar": "bad"})
        self.assertEqual(result["calmar"], 0.0)

    def test_none_values_via_decide(self) -> None:
        from strategy_research.core.strategy_acceptance import _coerce_metrics
        result = _coerce_metrics({"calmar": None, "trades": None})
        self.assertEqual(result["calmar"], 0.0)
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
