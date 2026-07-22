"""Tests for load_ohlcv_data — DuckDB OHLCV 读取 for bar-by-bar engine"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.core.db import (
    get_connection,
    init_db,
    load_ohlcv_data,
    load_price_data,
    save_ohlcv_data,
    save_price_data,
)


def _make_ohlcv(start: str = "2024-01-02", n_days: int = 10, base_price: float = 100.0) -> pd.DataFrame:
    """创建测试用 OHLCV DataFrame。"""
    dates = pd.bdate_range(start, periods=n_days)
    data = {
        "open": [base_price + i * 0.5 for i in range(n_days)],
        "high": [base_price + i * 0.5 + 1.0 for i in range(n_days)],
        "low": [base_price + i * 0.5 - 0.5 for i in range(n_days)],
        "close": [base_price + i * 0.5 + 0.5 for i in range(n_days)],
        "volume": [1000.0 + i * 100 for i in range(n_days)],
    }
    return pd.DataFrame(data, index=dates)


def _setup_workspace(tmp_path: Path, strategy: str, codes: list[str]) -> Path:
    """创建测试工作区并写入 OHLCV 数据。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    init_db(ws)
    for code in codes:
        ohlcv = _make_ohlcv(base_price=100.0 if code == "A" else 200.0)
        save_ohlcv_data(ws, strategy, code, ohlcv)
    return ws


# ============================================================
# load_ohlcv_data — 基本功能
# ============================================================


class TestLoadOhlcvBasic:
    def test_load_all_codes(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])
        result = load_ohlcv_data(ws, "s1")
        assert len(result) == 2
        assert "A" in result
        assert "B" in result

    def test_load_single_code(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])
        result = load_ohlcv_data(ws, "s1", codes=["A"])
        assert len(result) == 1
        assert "A" in result

    def test_returns_ohlcv_columns(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_index_is_datetime(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        assert isinstance(result["A"].index, pd.DatetimeIndex)

    def test_numeric_types(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        for col in ["open", "high", "low", "close", "volume"]:
            assert pd.api.types.is_numeric_dtype(result["A"][col])

    def test_empty_strategy(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "nonexistent")
        assert result == {}


# ============================================================
# load_ohlcv_data — 过滤
# ============================================================


class TestLoadOhlcvFilter:
    def test_date_filter(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", start_date="2024-01-05")
        assert len(result["A"]) < 10

    def test_date_range(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", start_date="2024-01-03", end_date="2024-01-08")
        assert 3 <= len(result["A"]) <= 6

    def test_codes_filter(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B", "C"])
        result = load_ohlcv_data(ws, "s1", codes=["A", "C"])
        assert set(result.keys()) == {"A", "C"}


# ============================================================
# load_ohlcv_data — 数据完整性
# ============================================================


class TestLoadOhlcvIntegrity:
    def test_ohlcv_values_match_saved(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        ohlcv = _make_ohlcv(start="2024-01-02", n_days=5, base_price=50.0)
        save_ohlcv_data(ws, "s1", "TEST", ohlcv)

        result = load_ohlcv_data(ws, "s1", codes=["TEST"])
        df = result["TEST"]

        # 验证 close 值
        expected_close = [50.5, 51.0, 51.5, 52.0, 52.5]
        assert list(df["close"]) == pytest.approx(expected_close)

        # 验证 volume
        expected_vol = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0]
        assert list(df["volume"]) == pytest.approx(expected_vol)

    def test_high_gte_low(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["high"] >= df["low"]).all()

    def test_high_gte_close(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["high"] >= df["close"]).all()

    def test_low_lte_close(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["low"] <= df["close"]).all()


# ============================================================
# load_ohlcv_data vs load_price_data 一致性
# ============================================================


class TestOhlcvVsPriceData:
    def test_close_matches_price_panel(self, tmp_path):
        """load_ohlcv_data 的 close 应与 load_price_data 的 panel 一致。"""
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])

        ohlcv = load_ohlcv_data(ws, "s1")
        panel = load_price_data(ws, "s1")

        for code in ["A", "B"]:
            ohlcv_close = ohlcv[code]["close"]
            panel_close = panel[code]
            pd.testing.assert_series_equal(ohlcv_close, panel_close, check_names=False)

    def test_ohlcv_preserves_open_high_low(self, tmp_path):
        """save_price_data 会丢失 OHLV；save_ohlcv_data 保留。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)

        # save_price_data 只有 close
        close_panel = pd.DataFrame(
            {"A": [100.0, 101.0, 102.0]},
            index=pd.bdate_range("2024-01-02", periods=3),
        )
        save_price_data(ws, "s1", close_panel)

        # load_ohlcv_data 应该有 open=close, high=close, low=close, volume=0
        ohlcv = load_ohlcv_data(ws, "s1")
        df = ohlcv["A"]
        assert (df["open"] == df["close"]).all()
        assert (df["high"] == df["close"]).all()
        assert (df["low"] == df["close"]).all()
        assert (df["volume"] == 0.0).all()


# ============================================================
# load_ohlcv_data — 空输入
# ============================================================


class TestLoadOhlcvEmpty:
    def test_no_data_in_db(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        result = load_ohlcv_data(ws, "nonexistent")
        assert result == {}

    def test_empty_codes_list(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", codes=[])
        # Empty codes list means no IN filter → returns all
        assert len(result) == 1


# ============================================================
# load_ohlcv_data — 多策略隔离
# ============================================================


class TestLoadOhlcvIsolation:
    def test_different_strategies_isolated(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        save_ohlcv_data(ws, "s1", "A", _make_ohlcv(base_price=100.0))
        save_ohlcv_data(ws, "s2", "A", _make_ohlcv(base_price=200.0))

        r1 = load_ohlcv_data(ws, "s1")
        r2 = load_ohlcv_data(ws, "s2")

        assert r1["A"]["close"].iloc[0] == pytest.approx(100.5)
        assert r2["A"]["close"].iloc[0] == pytest.approx(200.5)