"""Tests for CLI --llm-* flags + integration."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from strategy_research.cli import (
    _LLM_PARENT,
    _cli_overrides_from_args,
    _cmd_llm_list_profiles,
    build_llm_config,
    main,
)


# ── Parent parser has all expected flags ─────────────────────────────


class TestLLMParentParser:
    def test_parent_parser_has_profile(self):
        ns = _LLM_PARENT.parse_args(["--llm-profile", "deepseek"])
        assert ns.llm_profile == "deepseek"

    def test_parent_parser_has_model(self):
        ns = _LLM_PARENT.parse_args(["--llm-model", "gpt-4o"])
        assert ns.llm_model == "gpt-4o"

    def test_parent_parser_has_base_url(self):
        ns = _LLM_PARENT.parse_args(["--llm-base-url", "https://x"])
        assert ns.llm_base_url == "https://x"

    def test_parent_parser_has_temperature(self):
        ns = _LLM_PARENT.parse_args(["--llm-temperature", "0.5"])
        assert ns.llm_temperature == 0.5

    def test_parent_parser_has_max_tokens(self):
        ns = _LLM_PARENT.parse_args(["--llm-max-tokens", "2048"])
        assert ns.llm_max_tokens == 2048

    def test_parent_parser_has_top_p(self):
        ns = _LLM_PARENT.parse_args(["--llm-top-p", "0.9"])
        assert ns.llm_top_p == 0.9

    def test_parent_parser_has_timeout(self):
        ns = _LLM_PARENT.parse_args(["--llm-timeout", "30"])
        assert ns.llm_timeout == 30.0

    def test_parent_parser_has_max_retries(self):
        ns = _LLM_PARENT.parse_args(["--llm-max-retries", "5"])
        assert ns.llm_max_retries == 5

    def test_parent_parser_has_seed(self):
        ns = _LLM_PARENT.parse_args(["--llm-seed", "42"])
        assert ns.llm_seed == 42

    def test_parent_parser_stream_true(self):
        ns = _LLM_PARENT.parse_args(["--llm-stream"])
        assert ns.llm_stream is True

    def test_parent_parser_no_stream(self):
        ns = _LLM_PARENT.parse_args(["--llm-no-stream"])
        assert ns.llm_stream is False

    def test_parent_parser_defaults_none(self):
        ns = _LLM_PARENT.parse_args([])
        assert ns.llm_profile is None
        assert ns.llm_model is None
        assert ns.llm_temperature is None
        assert ns.llm_stream is None

    def test_parent_parser_unknown_flag_rejected(self):
        with pytest.raises(SystemExit):
            _LLM_PARENT.parse_args(["--llm-bogus", "x"])


# ── Helper functions ─────────────────────────────────────────────────


class TestCliOverridesFromArgs:
    def test_extracts_llm_keys(self):
        ns = argparse.Namespace(
            llm_profile="deepseek",
            llm_temperature=0.3,
            llm_stream=True,
            strategy="foo",  # not llm_*
        )
        d = _cli_overrides_from_args(ns)
        assert d == {"llm_profile": "deepseek", "llm_temperature": 0.3, "llm_stream": True}

    def test_skips_none_values(self):
        ns = argparse.Namespace(
            llm_profile=None, llm_model="x", llm_temperature=None,
        )
        d = _cli_overrides_from_args(ns)
        assert d == {"llm_model": "x"}

    def test_none_input(self):
        assert _cli_overrides_from_args(None) == {}

    def test_empty_namespace(self):
        ns = argparse.Namespace()
        assert _cli_overrides_from_args(ns) == {}


class TestBuildLLMConfig:
    def test_no_args(self):
        cfg = build_llm_config()
        assert cfg.model == "gpt-4o-mini"

    def test_with_profile(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
profiles:
  custom:
    model: custom-model
    temperature: 0.42
""", encoding="utf-8")
        cfg = build_llm_config(profile="custom", cli_overrides={})
        # Note: yaml_path not passed; will use default ~/.quantnodes-research/llm.yaml
        # → falls back to code defaults
        assert cfg.model == "gpt-4o-mini"

    def test_with_profile_and_yaml_path(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
default_profile: custom
profiles:
  custom:
    model: custom-model
    temperature: 0.42
""", encoding="utf-8")
        from strategy_research.core.llm import LLMConfig
        # Direct LLMConfig.load with yaml_path
        cfg = LLMConfig.load(profile="custom", yaml_path=p)
        assert cfg.model == "custom-model"
        assert cfg.temperature == 0.42

    def test_cli_overrides(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
profiles:
  deepseek:
    model: deepseek-chat
    temperature: 0.3
""", encoding="utf-8")
        from strategy_research.core.llm import LLMConfig
        cfg = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_temperature": 0.05},
            yaml_path=p,
        )
        assert cfg.model == "deepseek-chat"  # from yaml
        assert cfg.temperature == 0.05  # CLI wins


class TestListProfiles:
    def test_no_yaml(self, tmp_path, monkeypatch, capsys):
        from strategy_research.core.llm import config as llm_config
        monkeypatch.setattr(llm_config, "DEFAULT_LLM_CONFIG_PATH",
                            tmp_path / "missing.yaml")
        result = _cmd_llm_list_profiles()
        assert result == 0
        captured = capsys.readouterr()
        assert "no llm.yaml found" in captured.out

    def test_with_yaml(self, tmp_path, monkeypatch, capsys):
        from strategy_research.core.llm import config as llm_config
        p = tmp_path / "llm.yaml"
        p.write_text("""
default_profile: deepseek
profiles:
  deepseek:
    model: deepseek-chat
  fast:
    model: gpt-4o-mini
""", encoding="utf-8")
        monkeypatch.setattr(llm_config, "DEFAULT_LLM_CONFIG_PATH", p)
        result = _cmd_llm_list_profiles()
        assert result == 0
        captured = capsys.readouterr()
        assert "deepseek" in captured.out
        assert "fast" in captured.out
        assert "deepseek *" in captured.out  # default marker


# ── Top-level CLI integration ────────────────────────────────────────


class TestMainCLI:
    def test_llm_list_profiles_exits_zero(self, tmp_path, monkeypatch, capsys):
        from strategy_research.core.llm import config as llm_config
        monkeypatch.setattr(llm_config, "DEFAULT_LLM_CONFIG_PATH",
                            tmp_path / "missing.yaml")
        with patch("sys.argv", ["prog", "--llm-list-profiles"]):
            assert main() == 0
        captured = capsys.readouterr()
        assert "LLM profiles" in captured.out

    def test_llm_list_profiles_skips_other_args(self, tmp_path, monkeypatch, capsys):
        from strategy_research.core.llm import config as llm_config
        monkeypatch.setattr(llm_config, "DEFAULT_LLM_CONFIG_PATH",
                            tmp_path / "missing.yaml")
        # Even with garbage subcommand, --llm-list-profiles short-circuits
        with patch("sys.argv", ["prog", "--llm-list-profiles", "run", "badpath"]):
            assert main() == 0

    def test_run_subcommand_parses_llm_flags(self):
        # Just verify argparse parses; don't actually run backtest
        with patch("sys.argv", [
            "prog", "run", "--strategy", "foo",
            "--llm-profile", "deepseek",
            "--llm-temperature", "0.3",
            "--llm-max-tokens", "1024",
        ]):
            # main() will fail later (no workspace) but should pass argparse
            # Just check it gets past argparse
            try:
                main()
            except SystemExit as exc:
                # SystemExit(0) from --llm-list-profiles OR SystemExit(2) from argparse error
                # We want exit code != 2 (which would mean argparse failed)
                assert exc.code != 2, "argparse rejected LLM flags"
            except Exception:
                # Any other exception means we got past argparse
                pass

    def test_evaluate_subcommand_parses_llm_flags(self):
        with patch("sys.argv", [
            "prog", "evaluate", "--strategy", "foo",
            "--llm-no-stream",
        ]):
            try:
                main()
            except SystemExit as exc:
                assert exc.code != 2, "argparse rejected --llm-no-stream"
            except Exception:
                pass

    def test_autoresearch_subcommand_parses_llm_flags(self):
        with patch("sys.argv", [
            "prog", "autoresearch",
            "--llm-profile", "fast",
            "--llm-stream",
        ]):
            try:
                main()
            except SystemExit as exc:
                assert exc.code != 2, "argparse rejected LLM flags"
            except Exception:
                pass

    def test_subcommand_without_llm_flags(self):
        with patch("sys.argv", ["prog", "init", "/tmp/nonexistent_ws_test"]):
            # init will create a workspace; should succeed or fail at fs level
            try:
                main()
            except Exception:
                pass

    def test_no_command_shows_help(self, capsys):
        with patch("sys.argv", ["prog"]):
            assert main() == 0
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "quantnodes-research" in captured.out


# ── End-to-end config propagation ────────────────────────────────────


class TestConfigPropagation:
    """Verify CLI flags → LLMConfig.load() → LLMConfig instance."""

    def test_flags_override_yaml(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
profiles:
  deepseek:
    model: deepseek-chat
    temperature: 0.3
""", encoding="utf-8")
        from strategy_research.core.llm import LLMConfig
        cfg = LLMConfig.load(
            profile="deepseek",
            cli_overrides={
                "llm_temperature": 0.05,
                "llm_max_tokens": 1024,
            },
            yaml_path=p,
        )
        assert cfg.model == "deepseek-chat"
        assert cfg.temperature == 0.05
        assert cfg.max_tokens == 1024

    def test_env_var_priority(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
profiles:
  deepseek:
    model: deepseek-chat
""", encoding="utf-8")
        from strategy_research.core.llm import LLMConfig
        cfg = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_model": "cli-model"},
            env={"OPENAI_MODEL": "env-model"},
            yaml_path=p,
        )
        assert cfg.model == "cli-model"  # CLI > env > yaml

    def test_4_layer_priority(self, tmp_path):
        p = tmp_path / "llm.yaml"
        p.write_text("""
default_profile: deepseek
profiles:
  default:
    model: default-model
  deepseek:
    model: deepseek-chat
    temperature: 0.3
    max_tokens: 8000
""", encoding="utf-8")
        from strategy_research.core.llm import LLMConfig
        # No args → yaml default_profile: deepseek
        cfg1 = LLMConfig.load(yaml_path=p)
        assert cfg1.model == "deepseek-chat"
        assert cfg1.temperature == 0.3
        # Explicit profile
        cfg2 = LLMConfig.load(profile="default", yaml_path=p)
        assert cfg2.model == "default-model"
        # env override
        cfg3 = LLMConfig.load(env={"OPENAI_MODEL": "env-model"}, yaml_path=p)
        assert cfg3.model == "env-model"
        # CLI override
        cfg4 = LLMConfig.load(
            cli_overrides={"llm_model": "cli-model"}, yaml_path=p,
        )
        assert cfg4.model == "cli-model"