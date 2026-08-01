"""Tests for provider-profile switching (llm.json ``profiles``).

Covers:
    - Profile merge priority: base < profile < QUANTNODES__LLM__* env
    - ``LLM_PROFILE`` env selection over ``active_profile``
    - Backward compat: llm.json without ``profiles`` key
    - ``env:VAR`` expansion for profile api_key
    - ``llm`` CLI: --use (atomic + backup + auto-create), --add-key,
      --list, and ``--llm-profile`` single-run override
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strategy_research.cli.commands import llm as llm_cmd
from strategy_research.core.llm import LLMConfig
from strategy_research.core.llm.config import (
    ENV_PROFILE_ACTIVE,
)

PROFILE_JSON = {
    "llm": {
        "active_profile": "minimax",
        "timeout": 300,
        "profiles": {
            "minimax": {
                "provider": "minimax",
                "model": "minimax-M3",
                "api_key": "env:MINIMAX_API_KEY",
                "base_url": "https://api.minimaxi.com/v1",
                "model_max_output_tokens": 32000,
            },
            "nvidia": {
                "provider": "nvidia",
                "model": "z-ai/glm-5.2",
                "api_key": "env:NVIDIA_API_KEY",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
        },
    }
}


@pytest.fixture
def fake_llm_json(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "llm.json"
    monkeypatch.setattr(
        "strategy_research.core.llm.config._resolve_bridge_path",
        lambda env: p,
    )
    return p


@pytest.fixture
def clear_env(monkeypatch):
    for k in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "LLM_API_KEY", ENV_PROFILE_ACTIVE,
        "MINIMAX_API_KEY", "NVIDIA_API_KEY",
        "QUANTNODES__LLM__PROVIDER", "QUANTNODES__LLM__MODEL",
        "QUANTNODES__LLM__BASE_URL", "QUANTNODES__LLM__API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


# ── Profile resolution (config layer) ────────────────────────────────


class TestProfileResolution:
    def test_active_profile_merges_over_base(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-test")
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"
        assert cfg.model == "minimax-M3"
        assert cfg.base_url == "https://api.minimaxi.com/v1"
        assert cfg.api_key == "sk-minimax-test"
        assert cfg.max_tokens == 32000  # profile-provided
        assert cfg.timeout_s == 300.0   # base-level, untouched

    def test_profile_env_var_expansion(self, clear_env, fake_llm_json, monkeypatch):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-nvidia-test")
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "nvidia")
        cfg = LLMConfig.load()
        assert cfg.provider == "nvidia"
        assert cfg.api_key == "nvapi-nvidia-test"

    def test_llm_profile_env_beats_active_profile(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "nvidia")  # file says minimax
        cfg = LLMConfig.load()
        assert cfg.provider == "nvidia"

    def test_bridge_env_override_beats_profile(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("QUANTNODES__LLM__PROVIDER", "openai")
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        # but profile fields for non-overridden keys still apply
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_profile_unknown_falls_back_to_base(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "ghost",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "profiles": {},
            }
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o-mini"

    def test_old_format_no_profiles_still_works(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps({
            "llm": {"provider": "minimax", "model": "minimax-M3"},
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"

    def test_active_profile_none_uses_base(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"  # file's active_profile still applies

    def test_profiles_keys_never_leak_into_config(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        cfg = LLMConfig.load()
        cfg_dict = cfg.masked_dict()
        assert "profiles" not in cfg_dict
        assert "active_profile" not in cfg_dict


# ── CLI: llm command ─────────────────────────────────────────────────


@pytest.fixture
def cli_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    llm_json = tmp_path / "llm.json"
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(llm_cmd, "LLM_JSON_PATH", llm_json)
    monkeypatch.setattr(llm_cmd, "DOTENV_PATH", dotenv)
    monkeypatch.setattr(
        "strategy_research.core.llm.config._resolve_bridge_path",
        lambda env: llm_json,
    )
    return llm_json, dotenv


class TestCliUse:
    def test_use_switches_active_profile_atomically(
        self, clear_env, cli_paths, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
        rc = llm_cmd._action_use("nvidia")
        assert rc == 0
        data = json.loads(llm_json.read_text())
        assert data["llm"]["active_profile"] == "nvidia"
        # other top-level keys survive
        assert data["llm"]["timeout"] == 300

    def test_use_backs_up_before_switch(self, clear_env, cli_paths, monkeypatch):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        rc = llm_cmd._action_use("minimax")
        assert rc == 0
        backups = sorted(llm_json.parent.glob("llm.json.bak-*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text())["llm"]["active_profile"] == "minimax"

    def test_use_autocreates_profile_from_defaults(
        self, clear_env, cli_paths, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({
            "llm": {"active_profile": "minimax", "timeout": 300},
        }))
        rc = llm_cmd._action_use("nvidia")
        assert rc == 0
        data = json.loads(llm_json.read_text())
        profile = data["llm"]["profiles"]["nvidia"]
        assert profile["provider"] == "nvidia"
        assert profile["api_key"] == "env:NVIDIA_API_KEY"
        assert profile["base_url"] == "https://integrate.api.nvidia.com/v1"

    def test_use_unknown_provider_errors(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        assert llm_cmd._action_use("ghost") == 1

    def test_use_warns_when_key_missing(
        self, clear_env, cli_paths, capsys
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        rc = llm_cmd._action_use("minimax")
        assert rc == 0
        assert "未设置" in capsys.readouterr().out


class TestCliAddKey:
    def test_add_key_writes_dotenv(self, clear_env, cli_paths, monkeypatch):
        llm_json, dotenv = cli_paths
        dotenv.write_text("EXISTING=1\n", encoding="utf-8")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-qwen-123")
        assert llm_cmd._action_add_key("qwen") == 0
        content = dotenv.read_text(encoding="utf-8")
        assert "QWEN_API_KEY=sk-qwen-123" in content
        assert "EXISTING=1" in content  # other lines preserved

    def test_add_key_empty_cancels(self, clear_env, cli_paths, monkeypatch):
        _, dotenv = cli_paths
        monkeypatch.setattr("getpass.getpass", lambda prompt: "   ")
        assert llm_cmd._action_add_key("qwen") == 1
        assert not dotenv.exists()


class TestCliList:
    def test_list_marks_active_and_key_state(
        self, clear_env, cli_paths, capsys, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-x")
        assert llm_cmd._action_list() == 0
        out = capsys.readouterr().out
        assert "* minimax" in out
        assert "MINIMAX_API_KEY" in out
        assert "NVIDIA_API_KEY" in out


class TestCliProfileFlag:
    def test_llm_profile_flag_sets_env(
        self, clear_env, monkeypatch
    ):
        from types import SimpleNamespace

        from strategy_research.cli.llm_config import build_llm_config
        args = SimpleNamespace(llm_profile="nvidia")
        build_llm_config(args)
        assert os.environ.get(ENV_PROFILE_ACTIVE) == "nvidia"

    def test_llm_profile_excluded_from_cli_overrides(self, monkeypatch):
        from types import SimpleNamespace

        from strategy_research.cli.llm_config import _cli_overrides_from_args
        args = SimpleNamespace(llm_profile="nvidia", llm_model="x")
        out = _cli_overrides_from_args(args)
        assert out == {"llm_model": "x"}
