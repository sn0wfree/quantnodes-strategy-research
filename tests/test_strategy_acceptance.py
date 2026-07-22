"""Tests for core.strategy_acceptance — dual-layer keep/discard decision (P6 Step 0).

Covers:
    - HardThresholdRule per-metric checks
    - decide() happy path (hard pass)
    - decide() hard fail (regardless of LLM)
    - decide() LLM veto on hard pass
    - decide() LLM boost on hard pass
    - decide() stagnation override
    - decide() with custom config (require_all_hard=False)
    - load_config 4-layer merge (CLI > workspace > user > defaults)
    - LLMEvaluator verdict parsing (valid JSON, malformed, network failure)
    - CLI: `quantnodes-research accept` argparse + handler
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.strategy_acceptance import (
    DEFAULT_CONFIG,
    AcceptanceConfig,
    AcceptanceDecision,
    decide,
    load_config,
)
from strategy_research.core.strategy_acceptance.cli import (
    add_accept_subparsers,
    cmd_accept,
)
from strategy_research.core.strategy_acceptance.llm_eval import (
    LLMEvaluator,
    LLMEvaluatorError,
    evaluate_or_skip,
)
from strategy_research.core.strategy_acceptance.rules import (
    HardThresholdRule,
    RuleResult,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


def _good_metrics() -> dict:
    """Metrics that pass all hard thresholds with default config."""
    return {
        "calmar": 1.2,
        "sharpe": 0.8,
        "max_dd": -0.10,
        "ann_return": 0.15,
        "ann_vol": 0.20,
        "sortino": 1.5,
        "turnover": 0.5,
        "win_rate": 0.55,
        "trades": 50,
    }


def _bad_metrics() -> dict:
    """Metrics that fail most hard thresholds."""
    return {
        "calmar": 0.1,
        "sharpe": -0.5,
        "max_dd": -0.40,
        "ann_return": -0.05,
        "trades": 5,
    }


# ─── HardThresholdRule ───────────────────────────────────────────────


class TestHardThresholdRule:
    def test_all_pass(self):
        rule = HardThresholdRule()
        result = rule.check(_good_metrics(), DEFAULT_CONFIG)
        assert result.passed is True
        assert result.detail == {
            "calmar": True,
            "sharpe": True,
            "max_dd": True,
            "ann_return": True,
            "trades": True,
        }

    def test_calmar_fails(self):
        rule = HardThresholdRule()
        m = _good_metrics()
        m["calmar"] = 0.2  # < 0.5
        result = rule.check(m, DEFAULT_CONFIG)
        assert result.detail["calmar"] is False
        assert result.passed is False

    def test_max_dd_negative_boundary(self):
        rule = HardThresholdRule()
        m = _good_metrics()
        m["max_dd"] = -0.16  # < -0.15 threshold
        result = rule.check(m, DEFAULT_CONFIG)
        assert result.detail["max_dd"] is False

    def test_max_dd_exactly_at_threshold_passes(self):
        rule = HardThresholdRule()
        m = _good_metrics()
        m["max_dd"] = -0.15  # exactly at threshold
        result = rule.check(m, DEFAULT_CONFIG)
        assert result.detail["max_dd"] is True

    def test_trades_fails(self):
        rule = HardThresholdRule()
        m = _good_metrics()
        m["trades"] = 10
        result = rule.check(m, DEFAULT_CONFIG)
        assert result.detail["trades"] is False
        assert result.passed is False

    def test_ann_return_zero_threshold_disables(self):
        rule = HardThresholdRule()
        m = _good_metrics()
        m["ann_return"] = -10.0  # would fail, but threshold is 0.0 → disabled
        result = rule.check(m, DEFAULT_CONFIG)
        assert result.detail["ann_return"] is True

    def test_ann_return_nonzero_threshold_enforced(self):
        rule = HardThresholdRule()
        cfg = DEFAULT_CONFIG.with_overrides(hard_ann_return_min=0.05)
        m = _good_metrics()
        m["ann_return"] = 0.02  # < 0.05
        result = rule.check(m, cfg)
        assert result.detail["ann_return"] is False

    def test_require_all_hard_false_any_passes(self):
        rule = HardThresholdRule()
        cfg = DEFAULT_CONFIG.with_overrides(require_all_hard=False)
        m = _bad_metrics()  # most fail
        # calmar=0.1, sharpe=-0.5, max_dd=-0.40, ann_return=ok, trades=5
        # With "any", ann_return passes (ann_return check is disabled at 0.0),
        # so should pass.
        result = rule.check(m, cfg)
        assert result.passed is True


# ─── decide() ─────────────────────────────────────────────────────────


class TestDecide:
    def test_hard_pass_no_llm_accepts(self):
        d = decide(_good_metrics())
        assert d.accept is True
        assert d.hard_passed is True
        assert d.llm_passed is None
        assert d.stagnation_triggered is False
        assert "LLM layer not invoked" in d.reason

    def test_hard_fail_rejects_regardless_of_llm(self):
        d = decide(
            _bad_metrics(),
            llm_verdict={"passed": True, "score": 1.0, "reason": "best ever"},
        )
        assert d.accept is False
        assert d.hard_passed is False
        assert d.llm_passed is True  # LLM verdict was provided
        assert "hard threshold failed" in d.reason

    def test_llm_veto_rejects_even_when_hard_passes(self):
        d = decide(
            _good_metrics(),
            llm_verdict={"passed": False, "score": 0.2, "reason": "looks fishy"},
        )
        assert d.accept is False
        assert d.hard_passed is True
        assert d.llm_passed is False
        assert "LLM rejected" in d.reason

    def test_llm_boost_accepts_when_hard_passes(self):
        d = decide(
            _good_metrics(),
            llm_verdict={"passed": True, "score": 0.8, "reason": "solid"},
        )
        assert d.accept is True
        assert d.hard_passed is True
        assert d.llm_passed is True
        assert "hard + LLM passed" in d.reason

    def test_stagnation_forces_accept(self):
        d = decide(
            _bad_metrics(),
            stagnation_count=DEFAULT_CONFIG.stagnation_patience,
        )
        assert d.accept is True
        assert d.stagnation_triggered is True
        assert "stagnation" in d.reason

    def test_stagnation_below_patience_does_not_trigger(self):
        d = decide(_bad_metrics(), stagnation_count=3)
        assert d.accept is False
        assert d.stagnation_triggered is False

    def test_to_dict_serializable(self):
        d = decide(_good_metrics(), llm_verdict={"passed": True, "score": 0.7, "reason": "ok"})
        out = d.to_dict()
        # Round-trip through JSON to verify serializability
        json.dumps(out)  # must not raise
        assert out["accept"] is True
        assert out["hard_passed"] is True
        assert out["llm_passed"] is True
        assert out["llm_detail"]["score"] == 0.7

    def test_missing_metrics_default_to_zero(self):
        d = decide({})  # empty metrics → all fail
        assert d.accept is False
        assert d.hard_passed is False
        assert d.hard_detail["calmar"] is False
        assert d.hard_detail["sharpe"] is False


# ─── load_config ─────────────────────────────────────────────────────


class TestLoadConfig:
    def test_defaults(self):
        cfg = load_config()
        assert cfg.hard_calmar_min == DEFAULT_CONFIG.hard_calmar_min
        assert cfg.llm_enabled == DEFAULT_CONFIG.llm_enabled

    def test_cli_overrides_take_precedence(self, tmp_path: Path):
        cfg = load_config(cli_overrides={"hard_calmar_min": 0.7})
        assert cfg.hard_calmar_min == 0.7
        assert cfg.hard_sharpe_min == DEFAULT_CONFIG.hard_sharpe_min

    def test_workspace_overrides_defaults(self, tmp_path: Path):
        ws_yaml = tmp_path / "acceptance.yaml"
        ws_yaml.write_text("hard_calmar_min: 0.8\n", encoding="utf-8")
        cfg = load_config(workspace_config=ws_yaml)
        assert cfg.hard_calmar_min == 0.8

    def test_cli_overrides_workspace(self, tmp_path: Path):
        ws_yaml = tmp_path / "acceptance.yaml"
        ws_yaml.write_text("hard_calmar_min: 0.8\n", encoding="utf-8")
        cfg = load_config(
            cli_overrides={"hard_calmar_min": 0.9},
            workspace_config=ws_yaml,
        )
        assert cfg.hard_calmar_min == 0.9

    def test_missing_workspace_silent(self, tmp_path: Path):
        # No exception when workspace_config does not exist
        cfg = load_config(workspace_config=tmp_path / "nonexistent.yaml")
        assert cfg.hard_calmar_min == DEFAULT_CONFIG.hard_calmar_min

    def test_unknown_keys_ignored(self, tmp_path: Path):
        ws_yaml = tmp_path / "acceptance.yaml"
        ws_yaml.write_text("hard_calmar_min: 0.9\ntotally_made_up_field: 999\n",
                           encoding="utf-8")
        cfg = load_config(workspace_config=ws_yaml)
        assert cfg.hard_calmar_min == 0.9
        # No attribute access needed — silently dropped.

    def test_user_config_loaded_when_present(self, tmp_path: Path, monkeypatch):
        user_yaml = tmp_path / "user_acceptance.yaml"
        user_yaml.write_text("hard_calmar_min: 1.0\n", encoding="utf-8")
        cfg = load_config(user_config=user_yaml)
        assert cfg.hard_calmar_min == 1.0


# ─── LLMEvaluator ────────────────────────────────────────────────────


class TestLLMEvaluator:
    def _make_client(self, content: str) -> MagicMock:
        client = MagicMock()
        response = MagicMock()
        response.content = content
        client.chat.return_value = response
        return client

    def test_valid_json_passes(self):
        client = self._make_client(
            '{"passed": true, "score": 0.8, "reason": "looks great", '
            '"concerns": ["high turnover"]}'
        )
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0})
        assert v["passed"] is True
        assert v["score"] == 0.8
        assert v["reason"] == "looks great"
        assert v["concerns"] == ["high turnover"]
        assert "raw" in v

    def test_score_clamped_to_unit_interval(self):
        client = self._make_client('{"passed": true, "score": 1.5, "reason": "yay"}')
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0})
        assert v["score"] == 1.0

    def test_malformed_json_fails_safely(self):
        client = self._make_client("not json at all")
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0})
        assert v["passed"] is False
        assert v["score"] == 0.0
        assert "did not contain JSON" in v["reason"]

    def test_natural_json_extraction(self):
        """LLM sometimes wraps JSON in prose — extractor should find the {…}."""
        client = self._make_client(
            'Sure! Here is my verdict:\n'
            '{"passed": true, "score": 0.7, "reason": "ok"}\n'
            'Let me know if you need more detail.'
        )
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0})
        assert v["passed"] is True
        assert v["score"] == 0.7

    def test_network_error_returns_fail_verdict(self):
        client = MagicMock()
        client.chat.side_effect = RuntimeError("connection refused")
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0})
        assert v["passed"] is False
        assert "connection refused" in v["reason"]

    def test_cfg_threshold_overrides_score(self):
        client = self._make_client('{"passed": true, "score": 0.3, "reason": "low"}')
        cfg = DEFAULT_CONFIG.with_overrides(llm_score_threshold=0.5)
        v = LLMEvaluator(client=client).evaluate({"calmar": 1.0}, cfg=cfg)
        # score=0.3 < threshold=0.5 → passed should be False
        assert v["passed"] is False
        # raw score preserved for audit
        assert v["score"] == 0.3


class TestEvaluateOrSkip:
    def test_disabled_returns_none(self):
        cfg = DEFAULT_CONFIG.with_overrides(llm_enabled=False)
        assert evaluate_or_skip({"calmar": 1.0}, None, cfg) is None

    def test_enabled_with_passed_client(self):
        """When enabled and client passed in, uses it directly."""
        cfg = DEFAULT_CONFIG.with_overrides(llm_enabled=True)
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.content = '{"passed": true, "score": 0.7, "reason": "ok"}'
        fake_client.chat.return_value = fake_response
        v = evaluate_or_skip({"calmar": 1.0}, None, cfg, client=fake_client)
        assert v is not None
        assert v["passed"] is True
        assert v["score"] == 0.7

    def test_enabled_no_client_returns_fail_when_no_key(self, monkeypatch):
        """When enabled and no client + no API key, returns fail verdict (not raise)."""
        cfg = DEFAULT_CONFIG.with_overrides(llm_enabled=True)
        # Force LLMConfig.load() to return a config with no api_key
        import strategy_research.core.strategy_acceptance.llm_eval as mod
        stub_cfg = MagicMock()
        stub_cfg.api_key = ""  # simulate missing key
        monkeypatch.setattr(mod, "LLMConfig", MagicMock(load=MagicMock(return_value=stub_cfg)),
                            raising=False)
        v = evaluate_or_skip({"calmar": 1.0}, None, cfg)
        # Should return a fail verdict (not raise)
        assert v is not None
        assert v["passed"] is False
        assert "init failed" in v["reason"] or "api_key" in v["reason"].lower()


# ─── CLI ──────────────────────────────────────────────────────────────


def _make_args(**kwargs) -> argparse.Namespace:
    base = dict(
        metrics_file="dummy.json",
        workspace_config=None,
        llm_verdict=None,
        invoke_llm=False,
        stagnation_count=0,
        hard_calmar_min=None,
        hard_sharpe_min=None,
        hard_max_dd_min=None,
        hard_trades_min=None,
        hard_ann_return_min=None,
        llm_enabled=None,
        llm_score_threshold=None,
        stagnation_patience=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_accept_subparsers(sub)
    return parser


class TestAcceptCLI:
    def test_subparser_registered(self):
        parser = _make_parser()
        ns = parser.parse_args(["accept", "--metrics-file", "x.json"])
        assert ns.command == "accept"
        assert ns.metrics_file == "x.json"

    def test_metrics_file_missing_errors(self, tmp_path: Path, capsys):
        args = _make_args(metrics_file=str(tmp_path / "nope.json"))
        rc = cmd_accept(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "metrics file not found" in err

    def test_metrics_file_malformed_errors(self, tmp_path: Path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        args = _make_args(metrics_file=str(bad))
        rc = cmd_accept(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "could not parse" in err

    def test_good_run_accepts(self, tmp_path: Path, capsys):
        m = tmp_path / "good.json"
        m.write_text(json.dumps(_good_metrics()), encoding="utf-8")
        args = _make_args(metrics_file=str(m))
        rc = cmd_accept(args)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert rc == 0
        assert result["accept"] is True
        assert result["hard_passed"] is True

    def test_bad_run_rejects_with_rc_3(self, tmp_path: Path, capsys):
        m = tmp_path / "bad.json"
        m.write_text(json.dumps(_bad_metrics()), encoding="utf-8")
        args = _make_args(metrics_file=str(m))
        rc = cmd_accept(args)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert rc == 3
        assert result["accept"] is False
        assert result["hard_passed"] is False

    def test_cli_overrides_applied(self, tmp_path: Path, capsys):
        m = tmp_path / "m.json"
        # calmar=0.6 would fail default (0.5) but pass with --hard-calmar-min=0.5
        # actually 0.6 >= 0.5 passes by default; use a different threshold
        metrics = _good_metrics()
        metrics["calmar"] = 0.55  # 0.55 >= 0.5 passes default
        m.write_text(json.dumps(metrics), encoding="utf-8")
        args = _make_args(metrics_file=str(m), hard_calmar_min=0.6)
        rc = cmd_accept(args)
        out = capsys.readouterr().out
        result = json.loads(out)
        # With min=0.6 and calmar=0.55 → fail
        assert rc == 3
        assert result["hard_detail"]["calmar"] is False

    def test_llm_verdict_json_invalid(self, tmp_path: Path, capsys):
        m = tmp_path / "m.json"
        m.write_text(json.dumps(_good_metrics()), encoding="utf-8")
        args = _make_args(metrics_file=str(m), llm_verdict="not json")
        rc = cmd_accept(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not valid JSON" in err

    def test_stagnation_flag(self, tmp_path: Path, capsys):
        m = tmp_path / "m.json"
        m.write_text(json.dumps(_bad_metrics()), encoding="utf-8")
        args = _make_args(
            metrics_file=str(m),
            stagnation_count=DEFAULT_CONFIG.stagnation_patience,
        )
        rc = cmd_accept(args)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["stagnation_triggered"] is True
        assert result["accept"] is True