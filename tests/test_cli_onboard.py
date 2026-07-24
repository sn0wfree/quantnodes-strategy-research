"""Tests for ``cli.onboard`` — onboarding wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.cli.onboard import (
    BACK,
    CANCEL,
    PROVIDERS,
    TIMEOUT_CHOICES,
    _finalize,
    _render_env,
    _save_partial,
    is_onboarded,
    run_onboarding,
)


@pytest.fixture
def fresh_env_dir(tmp_path, monkeypatch):
    """Use a fresh env_dir for each test (don't touch ~/.quantnodes/strategy_research)."""
    d = tmp_path / ".quantnodes" / "strategy_research"
    d.mkdir(parents=True)
    return d


# ─── Sentinels + catalog ───────────────────────────────────────────────


class TestCatalog:
    def test_providers_non_empty(self):
        assert len(PROVIDERS) >= 3

    def test_provider_keys_unique(self):
        keys = [p.key for p in PROVIDERS]
        assert len(keys) == len(set(keys))

    def test_provider_has_required_fields(self):
        for p in PROVIDERS:
            assert p.label
            assert p.default_model
            assert p.base_url
            assert isinstance(p.suggested_models, tuple)

    def test_timeout_choices(self):
        assert len(TIMEOUT_CHOICES) >= 2
        for choice in TIMEOUT_CHOICES:
            assert len(choice) == 2  # (value, label) pair


class TestSentinels:
    def test_back(self):
        assert BACK is not None

    def test_cancel(self):
        assert CANCEL is not None

    def test_back_and_cancel_distinct(self):
        assert BACK is not CANCEL


# ─── Filesystem helpers ────────────────────────────────────────────────


class TestRenderEnv:
    def test_empty_dict(self):
        assert _render_env({}) == "\n"

    def test_one_value(self):
        out = _render_env({"FOO": "bar"})
        assert "FOO=bar" in out

    def test_skip_falsy_values(self):
        out = _render_env({"FOO": "bar", "EMPTY": "", "NONE": "0"})
        assert "FOO=bar" in out
        assert "EMPTY" not in out  # empty string is filtered

    def test_order_preserved(self):
        out = _render_env({"Z": "1", "A": "2"})
        lines = out.strip().splitlines()
        assert lines.index("Z=1") < lines.index("A=2")


class TestSavePartial:
    def test_creates_partial_file(self, fresh_env_dir):
        values = {"LANGCHAIN_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}
        _save_partial(values, env_dir=fresh_env_dir)
        partial = fresh_env_dir / ".env.partial"
        assert partial.exists()
        content = partial.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in content
        assert "OPENAI_API_KEY=sk-test" in content


class TestFinalize:
    def test_writes_dot_env(self, fresh_env_dir):
        values = {"A": "1", "B": "2"}
        path = _finalize(values, env_dir=fresh_env_dir)
        assert path == fresh_env_dir / ".env"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "A=1" in content

    def test_no_partial_left(self, fresh_env_dir):
        _finalize({"A": "1"}, env_dir=fresh_env_dir)
        assert not (fresh_env_dir / ".env.partial").exists()


class TestIsOnboarded:
    def test_false_when_no_env(self, fresh_env_dir):
        assert is_onboarded(env_dir=fresh_env_dir) is False

    def test_true_after_finalize(self, fresh_env_dir):
        _finalize({"X": "1"}, env_dir=fresh_env_dir)
        assert is_onboarded(env_dir=fresh_env_dir) is True


# ─── Full flow ────────────────────────────────────────────────────────


class TestRunOnboarding:
    def test_minimal_openai(self, fresh_env_dir):
        inputs = [
            "OpenAI",  # provider label
            "",  # model → uses default
            "sk-test1234",  # API key
            "300",  # timeout
            "",  # tushare (empty → skip)
        ]
        result = run_onboarding(env_dir=fresh_env_dir, inputs=inputs)
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in content
        assert "OPENAI_API_KEY=sk-test1234" in content
        assert "OPENAI_BASE_URL=https://api.openai.com/v1" in content
        assert "LANGCHAIN_MODEL_NAME=gpt-4o" in content
        assert "TIMEOUT_SECONDS=300" in content
        assert "MAX_RETRIES=2" in content
        # Tushare token was empty → not in .env
        assert "TUSHARE_TOKEN" not in content

    def test_ollama_no_key(self, fresh_env_dir):
        inputs = [
            "Ollama",
            "llama3.3:70b",  # explicit model
            # Ollama has key_env=None → step 3 skipped
            "120",  # timeout
            "",  # tushare (empty)
        ]
        result = run_onboarding(env_dir=fresh_env_dir, inputs=inputs)
        content = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=ollama" in content
        assert "LANGCHAIN_MODEL_NAME=llama3.3:70b" in content
        # Ollama has base_env=None → no OLLAMA_BASE_URL written
        assert "OLLAMA_BASE_URL" not in content
        # No API key either
        assert "OLLAMA_API_KEY" not in content

    def test_skip_tushare(self, fresh_env_dir):
        inputs = [
            "OpenAI",
            "gpt-4o-mini",
            "sk-test",
            "120",
            # no tushare step at all
        ]
        result = run_onboarding(
            env_dir=fresh_env_dir, inputs=inputs, skip_tushare=True
        )
        content = result.read_text(encoding="utf-8")
        assert "TIMEOUT_SECONDS=120" in content
        # No tushare step ran
        assert "TUSHARE_TOKEN" not in content

    def test_tushare_when_provided(self, fresh_env_dir):
        inputs = [
            "OpenAI",
            "",
            "sk-test",
            "300",
            "tushare_token_xyz",  # provided
        ]
        result = run_onboarding(env_dir=fresh_env_dir, inputs=inputs)
        content = result.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in content

    def test_unknown_provider_raises(self, fresh_env_dir):
        inputs = ["NonexistentProvider", "", "key", "300", ""]
        with pytest.raises(ValueError):
            run_onboarding(env_dir=fresh_env_dir, inputs=inputs)

    def test_no_inputs_in_non_tty_raises(self, fresh_env_dir):
        with pytest.raises(RuntimeError, match="inputs"):
            run_onboarding(env_dir=fresh_env_dir, inputs=None)

    def test_exhausted_inputs_raises(self, fresh_env_dir):
        with pytest.raises(RuntimeError, match="ran out"):
            run_onboarding(env_dir=fresh_env_dir, inputs=[])
