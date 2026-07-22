"""Tests for engine/signals.py — SignalEngine abstract + ConstantWeightEngine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.core.engine.signals import (
    ConstantWeightEngine,
    SignalEngine,
)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def sample_data_map():
    dates = pd.bdate_range("2024-01-02", periods=10)
    return {
        "AAPL": pd.DataFrame({
            "open": np.random.rand(10) * 100,
            "high": np.random.rand(10) * 110,
            "low": np.random.rand(10) * 90,
            "close": np.random.rand(10) * 100,
            "volume": np.random.rand(10) * 1_000_000,
        }, index=dates),
        "GOOG": pd.DataFrame({
            "open": np.random.rand(10) * 100,
            "high": np.random.rand(10) * 110,
            "low": np.random.rand(10) * 90,
            "close": np.random.rand(10) * 100,
            "volume": np.random.rand(10) * 1_000_000,
        }, index=dates),
    }


@pytest.fixture
def different_length_data():
    """Two DataFrames with different date indexes."""
    dates_a = pd.bdate_range("2024-01-02", periods=10)
    dates_b = pd.bdate_range("2024-01-15", periods=5)
    return {
        "A": pd.DataFrame({"close": [1.0] * 10}, index=dates_a),
        "B": pd.DataFrame({"close": [2.0] * 5}, index=dates_b),
    }


# ============================================================
# SignalEngine abstract base
# ============================================================


class TestSignalEngineAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            SignalEngine()

    def test_subclass_must_implement_generate(self):
        class IncompleteEngine(SignalEngine):
            pass
        with pytest.raises(TypeError):
            IncompleteEngine()

    def test_subclass_with_generate_works(self):
        class WorkingEngine(SignalEngine):
            def generate(self, data_map):
                return {k: pd.Series(0.0, index=v.index) for k, v in data_map.items()}

        eng = WorkingEngine()
        dates = pd.bdate_range("2024-01-02", periods=3)
        df = pd.DataFrame({"close": [1, 2, 3]}, index=dates)
        result = eng.generate({"X": df})
        assert "X" in result
        assert len(result["X"]) == 3


# ============================================================
# ConstantWeightEngine
# ============================================================


class TestConstantWeightEngine:
    def test_basic_constant_weights(self, sample_data_map):
        weights = {"AAPL": 0.5, "GOOG": -0.3}
        eng = ConstantWeightEngine(weights)
        result = eng.generate(sample_data_map)
        assert result["AAPL"].iloc[0] == 0.5
        assert result["GOOG"].iloc[0] == -0.3

    def test_constant_across_all_bars(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.5})
        result = eng.generate(sample_data_map)
        # All values should be identical
        assert (result["AAPL"] == 0.5).all()

    def test_unknown_code_defaults_to_zero(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.5})  # no GOOG
        result = eng.generate(sample_data_map)
        assert (result["GOOG"] == 0.0).all()

    def test_empty_weights_all_zero(self, sample_data_map):
        eng = ConstantWeightEngine({})
        result = eng.generate(sample_data_map)
        assert (result["AAPL"] == 0.0).all()
        assert (result["GOOG"] == 0.0).all()

    def test_negative_weights(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": -1.0, "GOOG": -0.5})
        result = eng.generate(sample_data_map)
        assert (result["AAPL"] == -1.0).all()
        assert (result["GOOG"] == -0.5).all()

    def test_zero_weight(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.0})
        result = eng.generate(sample_data_map)
        assert (result["AAPL"] == 0.0).all()

    def test_index_matches_input(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.5})
        result = eng.generate(sample_data_map)
        assert result["AAPL"].index.equals(sample_data_map["AAPL"].index)

    def test_series_name(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.5})
        result = eng.generate(sample_data_map)
        assert result["AAPL"].name == "AAPL"

    def test_different_length_data(self, different_length_data):
        eng = ConstantWeightEngine({"A": 0.5, "B": 0.7})
        result = eng.generate(different_length_data)
        assert len(result["A"]) == 10
        assert len(result["B"]) == 5
        assert (result["A"] == 0.5).all()
        assert (result["B"] == 0.7).all()

    def test_empty_data_map(self):
        eng = ConstantWeightEngine({})
        result = eng.generate({})
        assert result == {}

    def test_dtype_float(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 0.5})
        result = eng.generate(sample_data_map)
        assert result["AAPL"].dtype == float


# ============================================================
# Edge cases
# ============================================================


class TestSignalEngineEdgeCases:
    def test_extreme_weights(self, sample_data_map):
        eng = ConstantWeightEngine({"AAPL": 1.0, "GOOG": -1.0})
        result = eng.generate(sample_data_map)
        assert result["AAPL"].max() == 1.0
        assert result["GOOG"].min() == -1.0

    def test_data_map_with_single_code(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        df = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=dates)
        eng = ConstantWeightEngine({"X": 0.3})
        result = eng.generate({"X": df})
        assert len(result) == 1
        assert (result["X"] == 0.3).all()