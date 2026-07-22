"""Tests for PR3 fixes — config_runner alpha_id/alpha_ids + run_card whitelist.

覆盖：
- G8: config_runner.FactorStrategy 支持 alpha_id / alpha_ids
- G11: run_card 白名单包含 run/strategy/action（不再生成空 config）
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest


# ============================================================
# 1. run_card 白名单修复 (G11)
# ============================================================


class TestRunCardWhitelist:
    """run_card 白名单应包含 run/strategy/action。"""

    def test_whitelist_includes_run_strategy_action(self):
        """SCHEMA_VERSION 升级 + 白名单含 run/strategy/action。"""
        from strategy_research.core.run_card import BACKTEST_SUMMARY_KEYS
        assert "run" in BACKTEST_SUMMARY_KEYS
        assert "strategy" in BACKTEST_SUMMARY_KEYS
        assert "action" in BACKTEST_SUMMARY_KEYS
        # 同时保留老的 7 个 key
        for k in ["codes", "start_date", "end_date", "interval",
                  "engine", "initial_cash", "source"]:
            assert k in BACKTEST_SUMMARY_KEYS, f"{k} should remain in whitelist"

    def test_run_card_includes_run_strategy_action(self, tmp_path: Path):
        """write_run_card(config={"run":..., "strategy":..., "action":...}) 应保留 3 个键。"""
        from strategy_research.core.run_card import write_run_card

        run_dir = tmp_path / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        card = write_run_card(
            run_dir,
            config={
                "run": "run_0001",
                "strategy": "test_strat",
                "action": "baseline",
            },
            metrics={"calmar": 0.5, "sharpe": 0.8},
        )

        # config 不再为空
        assert "run" in card["config"]
        assert "strategy" in card["config"]
        assert "action" in card["config"]
        assert card["config"]["run"] == "run_0001"
        assert card["config"]["strategy"] == "test_strat"
        assert card["config"]["action"] == "baseline"

    def test_run_card_schema_version_bumped(self, tmp_path: Path):
        """SCHEMA_VERSION 应升级到 0.2（白名单扩展）。"""
        from strategy_research.core.run_card import SCHEMA_VERSION, write_run_card

        assert SCHEMA_VERSION == "0.2", "schema version should be bumped to 0.2"

        run_dir = tmp_path / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        card = write_run_card(run_dir, config={"run": "x"}, metrics={})
        assert card["schema_version"] == "0.2"

    def test_run_card_md_contains_new_keys(self, tmp_path: Path):
        """生成的 Markdown 应包含 run/strategy/action 表格行。"""
        from strategy_research.core.run_card import write_run_card

        run_dir = tmp_path / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        write_run_card(
            run_dir,
            config={"run": "r1", "strategy": "s1", "action": "a1"},
            metrics={"calmar": 0.5},
        )

        md = (run_dir / "run_card.md").read_text(encoding="utf-8")
        assert "run" in md
        assert "strategy" in md
        assert "action" in md


# ============================================================
# 2. config_runner.FactorStrategy 支持 alpha_id / alpha_ids (G8)
# ============================================================


class TestFactorStrategyAlphaZoo:
    """FactorStrategy 应支持 3 种因子类型。"""

    def _make_panel(self, n_assets: int = 3, n_days: int = 30) -> pd.DataFrame:
        """构造简单价格面板。"""
        dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
        cols = [f"asset_{i:02d}" for i in range(n_assets)]
        np_random = __import__("numpy").random
        __import__("numpy").random.seed(42)
        data = np_random.uniform(10, 20, size=(n_days, n_assets))
        return pd.DataFrame(data, index=dates, columns=cols)

    def test_factor_strategy_accepts_code_factor(self):
        """方式 1: code 因子（表达式）应正常计算。"""
        from strategy_research.core.config_runner import FactorStrategy

        fs = FactorStrategy(
            factors=[{
                "name": "mom_20d",
                "code": "ts_return(close, 5)",
                "weight": 1.0,
            }],
            params={"top_n": 2, "weight_method": "equal"},
        )
        panel = self._make_panel()
        weights = fs.compute_weights(panel.index[10], panel, pd.Series())
        assert isinstance(weights, dict)
        assert len(weights) <= 2

    def test_factor_strategy_accepts_alpha_id_factor(self, monkeypatch: pytest.MonkeyPatch):
        """方式 2: alpha_id 因子（Alpha Zoo）应调用 compute_as_wide。"""
        from strategy_research.core import config_runner
        from strategy_research.core import alpha_zoo_adapter as aza_module

        class MockAlphaZoo:
            def compute_as_wide(self, alpha_id, prices, **kwargs):
                import numpy as np
                np.random.seed(42)
                return pd.DataFrame(
                    np.random.rand(*prices.shape),
                    index=prices.index,
                    columns=prices.columns,
                )

        monkeypatch.setattr(aza_module, "AlphaZooAdapter", MockAlphaZoo)

        fs = config_runner.FactorStrategy(
            factors=[{
                "name": "zoo_mom",
                "alpha_id": "gtja191_001",
                "weight": 1.0,
            }],
            params={"top_n": 2, "weight_method": "equal"},
        )
        panel = self._make_panel()
        weights = fs.compute_weights(panel.index[10], panel, pd.Series())
        assert isinstance(weights, dict)
        # 应选择 top_n 个资产并赋等权
        assert len(weights) == 2
        for w in weights.values():
            assert abs(w - 0.5) < 1e-9

    def test_factor_strategy_accepts_alpha_ids_combined(self, monkeypatch: pytest.MonkeyPatch):
        """方式 3: alpha_ids 组合因子应调用 compute_batch。"""
        from strategy_research.core import config_runner
        from strategy_research.core import alpha_zoo_adapter as aza_module

        class MockAlphaZoo:
            def compute_batch(self, alpha_ids, prices, **kwargs):
                import numpy as np
                np.random.seed(42)
                return pd.DataFrame({
                    aid: pd.Series(
                        np.random.rand(len(prices)),
                        index=prices.index,
                    )
                    for aid in alpha_ids
                })

        monkeypatch.setattr(aza_module, "AlphaZooAdapter", MockAlphaZoo)

        fs = config_runner.FactorStrategy(
            factors=[{
                "name": "composite",
                "alpha_ids": ["alpha101_001", "gtja191_005"],
                "combination": "equal",
                "weight": 1.0,
            }],
            params={"top_n": 2, "weight_method": "equal"},
        )
        panel = self._make_panel()
        weights = fs.compute_weights(panel.index[10], panel, pd.Series())
        assert isinstance(weights, dict)

    def test_factor_strategy_handles_empty_factor(self):
        """空 factors 列表应正常返回等权 top_n。"""
        from strategy_research.core.config_runner import FactorStrategy

        fs = FactorStrategy(
            factors=[],
            params={"top_n": 3, "weight_method": "equal"},
        )
        panel = self._make_panel(n_assets=3)
        weights = fs.compute_weights(panel.index[10], panel, pd.Series())
        # 空因子：scores 全 0，应返回前 top_n 个资产的等权
        assert len(weights) == 3
        for w in weights.values():
            assert abs(w - 1/3) < 1e-9

    def test_factor_strategy_skips_invalid_factor_gracefully(self, capsys):
        """无效因子（无 code / alpha_id / alpha_ids）应被跳过。"""
        from strategy_research.core.config_runner import FactorStrategy

        fs = FactorStrategy(
            factors=[
                {"name": "invalid", "weight": 1.0},  # 没有 code/alpha_id/alpha_ids
                {"name": "valid", "code": "ts_return(close, 5)", "weight": 1.0},
            ],
            params={"top_n": 2, "weight_method": "equal"},
        )
        panel = self._make_panel()
        weights = fs.compute_weights(panel.index[10], panel, pd.Series())
        # valid 因子应被计算
        assert isinstance(weights, dict)


# ============================================================
# 3. cmd_init --no-baseline 选项 (G10)
# ============================================================


class TestCmdInitNoBaseline:
    """cmd_init --no-baseline 应跳过 baseline 回测。"""

    def test_no_baseline_skips_run_creation(self, tmp_path: Path, monkeypatch):
        """--no-baseline 时不创建任何 run。"""
        from strategy_research.cli import cmd_init

        inputs = iter(["test_strat", "rotation", "calmar"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        ws = tmp_path / "ws"
        args = __import__("argparse").Namespace(
            path=str(ws), force=True, no_baseline=True,
        )
        rc = cmd_init(args)
        assert rc == 0

        runs_dir = ws / "strategies" / "test_strat" / "runs"
        runs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_"))
        # 应只有 results.tsv，无 run_XXXX 目录
        assert "run_0001" not in runs
        assert "run_0000" not in runs

    def test_default_runs_baseline(self, tmp_path: Path, monkeypatch):
        """默认（不带 --no-baseline）应跑 baseline。"""
        from strategy_research.cli import cmd_init

        inputs = iter(["test_strat", "rotation", "calmar"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        ws = tmp_path / "ws"
        args = __import__("argparse").Namespace(
            path=str(ws), force=True, no_baseline=False,
        )
        rc = cmd_init(args)
        assert rc == 0

        runs_dir = ws / "strategies" / "test_strat" / "runs"
        runs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_"))
        assert len(runs) >= 1, f"baseline 应创建至少 1 个 run，实际 {runs}"


# ============================================================
# 4. cmd_init 配置 config.yaml 完整字段 (G12)
# ============================================================


class TestCmdInitConfigYAML:
    """cmd_init 生成的 config.yaml 应包含 data / rebalance / risk 字段。"""

    def test_config_yaml_has_data_section(self, tmp_path: Path, monkeypatch):
        from strategy_research.cli import cmd_init

        inputs = iter(["test_strat", "rotation", "calmar"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        ws = tmp_path / "ws"
        args = __import__("argparse").Namespace(
            path=str(ws), force=True, no_baseline=True,
        )
        cmd_init(args)

        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))
        assert "workspace" in config
        assert "data" in config
        assert config["data"]["source"] == "duckdb"
        assert "rebalance" in config
        assert config["rebalance"]["freq"] == "M"
        assert "top_n" in config
        assert "max_weight" in config
        assert "weight_method" in config
        assert "cost" in config
        assert "risk" in config