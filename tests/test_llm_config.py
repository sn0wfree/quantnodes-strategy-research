"""Tests for LLMConfig 4-layer merge.

Covers:
    - Code defaults
    - YAML profile loading
    - Env var overrides
    - CLI overrides
    - Priority chain (CLI > env > yaml > defaults)
    - Profile switching (arg, env, yaml default_profile)
    - Error handling (unknown profile, malformed yaml)
    - YAML missing → silent fallback
    - Forward-compat (unknown yaml keys ignored)
    - .env loading (no-op when python-dotenv missing)
    - CLI flag mapping helpers
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from strategy_research.core.llm import LLMConfig
from strategy_research.core.llm.config import (
    DEFAULT_LLM_CONFIG_PATH,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_CONFIG_PATH,
    ENV_MODEL,
    ENV_PROFILE,
    _cli_to_overrides,
    _env_to_overrides,
    _yaml_default_profile,
    apply_api_key,
    get_default_profile,
    list_profiles,
    load_api_key_from_env,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    """YAML with multiple profiles + default_profile."""
    p = tmp_path / "llm.yaml"
    p.write_text(
        """
default_profile: deepseek
profiles:
  default:
    model: gpt-4o-mini
    temperature: 0.7
  deepseek:
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1
    temperature: 0.3
    max_tokens: 8000
  fast:
    model: gpt-4o-mini
    temperature: 0.5
    max_tokens: 2048
  with_unknown_field:
    model: x-model
    unknown_future_field: foo
    reasoning_effort: low
  with_list_stop:
    stop: ["<|end|>", "STOP"]
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def no_yaml(tmp_path: Path) -> Path:
    """Returns a non-existent path."""
    return tmp_path / "nonexistent.yaml"


# ── Code defaults ───────────────────────────────────────────────────


class TestCodeDefaults:
    def test_defaults_minimal(self):
        c = LLMConfig()
        assert c.profile == "default"
        assert c.model == "gpt-4o-mini"
        assert c.base_url == "https://api.openai.com/v1"
        assert c.api_key == ""
        assert c.temperature == 0.7
        assert c.top_p == 1.0
        assert c.max_tokens == 4096
        assert c.timeout_s == 60.0
        assert c.max_retries == 3
        assert c.stream is True
        assert c.tool_choice == "auto"
        assert c.parallel_tool_calls is True

    def test_with_config_returns_new_instance(self):
        c1 = LLMConfig()
        c2 = c1.with_config(temperature=0.3)
        assert c1.temperature == 0.7  # immutable
        assert c2.temperature == 0.3

    def test_to_dict_roundtrip(self):
        c = LLMConfig(temperature=0.5, model="x")
        d = c.to_dict()
        assert d["temperature"] == 0.5
        assert d["model"] == "x"

    def test_masked_dict_short_key(self):
        c = LLMConfig(api_key="abc")
        m = c.masked_dict()
        assert m["api_key"] == "***"

    def test_masked_dict_long_key(self):
        c = LLMConfig(api_key="sk-abc1234567xyz")
        m = c.masked_dict()
        assert m["api_key"] == "sk-a***7xyz"

    def test_masked_dict_empty_key(self):
        c = LLMConfig()
        m = c.masked_dict()
        assert m["api_key"] == ""


# ── YAML layer ──────────────────────────────────────────────────────


class TestYamlLayer:
    def test_load_profile_arg(self, sample_yaml: Path):
        c = LLMConfig.load(profile="deepseek", yaml_path=sample_yaml)
        assert c.model == "deepseek-chat"
        assert c.temperature == 0.3
        assert c.max_tokens == 8000
        assert c.profile == "deepseek"

    def test_load_default_profile(self, sample_yaml: Path):
        # No profile arg → uses default_profile: deepseek from yaml
        c = LLMConfig.load(yaml_path=sample_yaml)
        assert c.profile == "deepseek"
        assert c.model == "deepseek-chat"

    def test_explicit_default_profile(self, sample_yaml: Path):
        c = LLMConfig.load(profile="default", yaml_path=sample_yaml)
        assert c.model == "gpt-4o-mini"
        assert c.temperature == 0.7

    def test_unknown_profile_raises(self, sample_yaml: Path):
        with pytest.raises(ValueError, match="not found"):
            LLMConfig.load(profile="nonexistent", yaml_path=sample_yaml)

    def test_yaml_missing_silent(self, no_yaml: Path):
        c = LLMConfig.load(yaml_path=no_yaml)
        # Should use code defaults, not raise
        assert c.model == "gpt-4o-mini"
        assert c.profile == "default"

    def test_yaml_no_default_profile_key(self, tmp_path: Path):
        p = tmp_path / "llm.yaml"
        p.write_text("profiles:\n  myprof:\n    model: my-model\n", encoding="utf-8")
        # No explicit profile → falls back to 'default' silently
        c = LLMConfig.load(yaml_path=p)
        assert c.model == "gpt-4o-mini"  # code default
        assert c.profile == "default"

    def test_yaml_unknown_keys_ignored(self, sample_yaml: Path):
        c = LLMConfig.load(profile="with_unknown_field", yaml_path=sample_yaml)
        # Unknown keys silently ignored
        assert c.model == "x-model"
        assert not hasattr(c, "unknown_future_field")
        assert not hasattr(c, "reasoning_effort")

    def test_yaml_stop_field_as_list(self, sample_yaml: Path):
        c = LLMConfig.load(profile="with_list_stop", yaml_path=sample_yaml)
        assert c.stop == ("<|end|>", "STOP")
        assert isinstance(c.stop, tuple)

    def test_yaml_malformed_raises(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("profiles: [unclosed\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            LLMConfig.load(yaml_path=p)

    def test_yaml_root_not_mapping(self, tmp_path: Path):
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ValueError, match="root must be a mapping"):
            LLMConfig.load(yaml_path=p)

    def test_yaml_profiles_not_mapping(self, tmp_path: Path):
        p = tmp_path / "llm.yaml"
        p.write_text("profiles: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a mapping"):
            LLMConfig.load(profile="x", yaml_path=p)

    def test_yaml_profile_not_mapping(self, tmp_path: Path):
        p = tmp_path / "llm.yaml"
        p.write_text("profiles:\n  bad: 42\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a mapping"):
            LLMConfig.load(profile="bad", yaml_path=p)

    def test_yaml_explicit_unknown_raises_even_if_no_default(self, tmp_path: Path):
        p = tmp_path / "llm.yaml"
        p.write_text("profiles:\n  myprof:\n    model: x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not found"):
            LLMConfig.load(profile="doesnotexist", yaml_path=p)

    def test_yaml_explicit_existing_profile_works(self, tmp_path: Path):
        p = tmp_path / "llm.yaml"
        p.write_text("profiles:\n  myprof:\n    model: my-model\n", encoding="utf-8")
        c = LLMConfig.load(profile="myprof", yaml_path=p)
        assert c.model == "my-model"


# ── Env var layer ───────────────────────────────────────────────────


class TestEnvLayer:
    def test_openai_base_url(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"OPENAI_BASE_URL": "https://custom.api/v1"},
        )
        assert c.base_url == "https://custom.api/v1"

    def test_openai_model(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"OPENAI_MODEL": "gpt-4o"},
        )
        assert c.model == "gpt-4o"

    def test_strategy_research_llm_profile(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"STRATEGY_RESEARCH_LLM_PROFILE": "fast"},
        )
        assert c.profile == "fast"
        assert c.max_tokens == 2048

    def test_env_overrides_yaml(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"OPENAI_MODEL": "env-model", "OPENAI_BASE_URL": "https://env/v1"},
        )
        assert c.model == "env-model"
        assert c.base_url == "https://env/v1"

    def test_unknown_env_var_ignored(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"OPENAI_FAKE_VAR": "x", "RANDOM": "y"},
        )
        assert c.model == "deepseek-chat"  # from yaml default_profile

    def test_api_key_from_env(self, sample_yaml: Path):
        c = LLMConfig.load(
            yaml_path=sample_yaml,
            env={"OPENAI_API_KEY": "sk-test1234"},
        )
        assert c.api_key == "sk-test1234"


# ── CLI override layer ──────────────────────────────────────────────


class TestCliLayer:
    def test_cli_temperature(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_temperature": 0.1},
            yaml_path=sample_yaml,
        )
        assert c.temperature == 0.1

    def test_cli_max_tokens(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_max_tokens": 1024},
            yaml_path=sample_yaml,
        )
        assert c.max_tokens == 1024

    def test_cli_timeout_mapped_to_timeout_s(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_timeout": 30.0},
            yaml_path=sample_yaml,
        )
        assert c.timeout_s == 30.0

    def test_cli_stream_true(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_stream": True},
            yaml_path=sample_yaml,
        )
        assert c.stream is True

    def test_cli_no_stream_false_keeps_stream_true(self, sample_yaml: Path):
        # llm_no_stream=False should NOT change stream
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_no_stream": False},
            yaml_path=sample_yaml,
        )
        # yaml default for deepseek doesn't set stream, so it stays default True
        assert c.stream is True

    def test_cli_no_stream_true_disables(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_no_stream": True},
            yaml_path=sample_yaml,
        )
        assert c.stream is False

    def test_cli_none_values_ignored(self):
        c = LLMConfig.load(cli_overrides={"llm_model": None, "llm_temperature": None})
        assert c.model == "gpt-4o-mini"
        assert c.temperature == 0.7

    def test_cli_non_llm_keys_ignored(self):
        c = LLMConfig.load(
            cli_overrides={"command": "run", "strategy": "foo", "max_iter": 10}
        )
        assert c.model == "gpt-4o-mini"

    def test_cli_unknown_llm_keys_passed_through(self):
        # Forward-compat: unknown llm_* keys are passed through but
        # silently dropped by _merge_flat (since not in dataclass fields)
        c = LLMConfig.load(
            cli_overrides={"llm_unknown_field": "foo", "llm_model": "x"}
        )
        assert c.model == "x"


# ── Priority chain ──────────────────────────────────────────────────


class TestPriority:
    def test_cli_over_env(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={"llm_model": "cli-model"},
            env={"OPENAI_MODEL": "env-model"},
            yaml_path=sample_yaml,
        )
        assert c.model == "cli-model"

    def test_env_over_yaml(self, sample_yaml: Path):
        c = LLMConfig.load(
            profile="deepseek",
            env={"OPENAI_MODEL": "env-model"},
            yaml_path=sample_yaml,
        )
        assert c.model == "env-model"

    def test_yaml_over_defaults(self, sample_yaml: Path):
        c = LLMConfig.load(profile="deepseek", yaml_path=sample_yaml)
        assert c.temperature == 0.3  # from yaml
        assert LLMConfig().temperature == 0.7  # default

    def test_full_chain(self, sample_yaml: Path):
        # CLI wins everything
        c = LLMConfig.load(
            profile="deepseek",
            cli_overrides={
                "llm_temperature": 0.05,
                "llm_stream": False,
                "llm_seed": 999,
            },
            env={"OPENAI_MODEL": "env-m", "OPENAI_BASE_URL": "https://env/v1"},
            yaml_path=sample_yaml,
        )
        assert c.temperature == 0.05  # CLI
        assert c.stream is False  # CLI
        assert c.seed == 999  # CLI
        assert c.model == "env-m"  # env (CLI didn't set model)
        assert c.base_url == "https://env/v1"  # env


# ── Helpers ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_list_profiles(self, sample_yaml: Path):
        profiles = list_profiles(sample_yaml)
        assert set(profiles) == {"default", "deepseek", "fast", "with_unknown_field", "with_list_stop"}

    def test_list_profiles_no_file(self, tmp_path: Path):
        assert list_profiles(tmp_path / "missing.yaml") == []

    def test_get_default_profile(self, sample_yaml: Path):
        assert get_default_profile(sample_yaml) == "deepseek"

    def test_get_default_profile_no_file(self, tmp_path: Path):
        assert get_default_profile(tmp_path / "missing.yaml") == "default"

    def test_get_default_profile_malformed(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("[broken", encoding="utf-8")
        assert get_default_profile(p) == "default"

    def test_load_api_key_from_env(self):
        assert load_api_key_from_env({"OPENAI_API_KEY": "sk-x"}) == "sk-x"
        assert load_api_key_from_env({}) == ""

    def test_apply_api_key_fills_empty(self):
        c = LLMConfig()
        c2 = apply_api_key(c, env={"OPENAI_API_KEY": "sk-test"})
        assert c2.api_key == "sk-test"

    def test_apply_api_key_preserves_existing(self):
        c = LLMConfig(api_key="sk-existing")
        c2 = apply_api_key(c, env={"OPENAI_API_KEY": "sk-other"})
        assert c2.api_key == "sk-existing"

    def test_apply_api_key_no_env_key(self):
        c = LLMConfig()
        c2 = apply_api_key(c, env={})
        assert c2.api_key == ""

    def test_env_to_overrides_only_documented(self):
        env = {
            ENV_BASE_URL: "https://x",
            ENV_MODEL: "m",
            ENV_PROFILE: "p",
            "RANDOM": "y",
            "PATH": "/usr/bin",
        }
        o = _env_to_overrides(env)
        assert o == {"base_url": "https://x", "model": "m", "profile": "p"}

    def test_cli_to_overrides_all_mappings(self):
        cli = {
            "llm_model": "m",
            "llm_base_url": "b",
            "llm_temperature": 0.5,
            "llm_max_tokens": 100,
            "llm_top_p": 0.9,
            "llm_timeout": 30.0,
            "llm_max_retries": 5,
            "llm_seed": 42,
            "llm_stream": True,
            "llm_no_stream": False,
            "llm_profile": "deepseek",
            "llm_unknown_field": "foo",
            "command": "run",  # ignored
        }
        o = _cli_to_overrides(cli)
        assert o["model"] == "m"
        assert o["base_url"] == "b"
        assert o["temperature"] == 0.5
        assert o["max_tokens"] == 100
        assert o["top_p"] == 0.9
        assert o["timeout_s"] == 30.0
        assert o["max_retries"] == 5
        assert o["seed"] == 42
        assert o["stream"] is True
        assert o["profile"] == "deepseek"
        assert "command" not in o

    def test_cli_to_overrides_list_profiles_flag_ignored(self):
        cli = {"llm_list_profiles": True}
        assert _cli_to_overrides(cli) == {}

    def test_yaml_default_profile(self, sample_yaml: Path):
        assert _yaml_default_profile(sample_yaml) == "deepseek"

    def test_yaml_default_profile_missing(self, tmp_path: Path):
        assert _yaml_default_profile(tmp_path / "missing.yaml") == "default"


# ── Constants exposed ───────────────────────────────────────────────


class TestConstants:
    def test_default_path(self):
        assert DEFAULT_LLM_CONFIG_PATH == Path.home() / ".quantnodes-research" / "llm.yaml"

    def test_env_constants(self):
        assert ENV_API_KEY == "OPENAI_API_KEY"
        assert ENV_BASE_URL == "OPENAI_BASE_URL"
        assert ENV_MODEL == "OPENAI_MODEL"
        assert ENV_PROFILE == "STRATEGY_RESEARCH_LLM_PROFILE"
        assert ENV_CONFIG_PATH == "STRATEGY_RESEARCH_LLM_CONFIG"


# ── .env loading (best-effort, no-op when not installed) ────────────


class TestDotenvLoading:
    def test_load_with_dotenv_false(self, sample_yaml: Path):
        # Should still work
        c = LLMConfig.load(yaml_path=sample_yaml, load_dotenv=False)
        assert c.profile == "deepseek"

    def test_dotenv_not_installed_graceful(self, sample_yaml: Path):
        # Force dotenv to be unavailable
        with patch.dict("sys.modules", {"dotenv": None}):
            # Should not raise; should use os.environ
            c = LLMConfig.load(yaml_path=sample_yaml, load_dotenv=True)
            assert c.profile == "deepseek"