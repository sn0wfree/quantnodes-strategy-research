"""Tests for config_runner.py on_risk_check import path.

Regression test for the bug where ``from .backtest_utils import`` failed
because the module actually lives at ``strategy_research.core.utils.backtest_utils``.
"""
from __future__ import annotations

import pandas as pd
import pytest


class TestConfigRunnerImport:
    """Verify the on_risk_check import path is correct."""

    def test_on_risk_check_imports_backtest_utils(self):
        """The on_risk_check method should resolve backtest_utils without error."""
        from strategy_research.core.config_runner import FactorStrategy

        inst = FactorStrategy.__new__(FactorStrategy)
        inst.params = {"max_weight": 0.25}

        # This must not raise ModuleNotFoundError
        weights = {"A": 0.4, "B": 0.4, "C": 0.4}  # over max_weight
        nav = pd.Series([1.0, 1.1, 1.05])
        date = pd.Timestamp("2024-01-01")

        result = inst.on_risk_check(weights, nav, date)
        assert isinstance(result, dict)
        # After apply_max_weight + normalize, sum should be 1.0
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_no_legacy_backtest_utils_module(self):
        """Confirm there is no module at strategy_research.core.backtest_utils
        (so the original import would have failed).
        """
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("strategy_research.core.backtest_utils")

    def test_correct_module_path_exists(self):
        """The actual module path is strategy_research.core.utils.backtest_utils."""
        from strategy_research.core.utils import backtest_utils

        assert hasattr(backtest_utils, "apply_max_weight")
        assert hasattr(backtest_utils, "normalize_weights")
