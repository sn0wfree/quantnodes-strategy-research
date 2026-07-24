"""Tests for the v0.5.0 init wizard.

Covers:

* :func:`strategy_research.cli.onboard.run_onboarding` (the prompt-toolkit
  5-step wizard used by both init paths).
* :func:`strategy_research.cli.onboard.is_onboarded`.
* :func:`strategy_research.cli.onboard._save_partial` and
  :func:`_finalize` (atomic .env.partial → .env, chmod 0o600).
* :func:`strategy_research.cli._auto_onboard._maybe_run_onboarding`
  (the auto-trigger on bare ``quantnodes-research`` invocations).
* :func:`strategy_research.cli._auto_onboard._migrate_legacy_env`
  (one-shot copy of ``~/.strategy-research/.env``).
* :func:`strategy_research.cli.__init__.cmd_run_onboarding` (the explicit
  CLI path driven by ``quantnodes-research init``).
"""
from __future__ import annotations

import argparse
import io
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from strategy_research.cli._auto_onboard import (
    _DEFAULT_ENV_DIR,
    _DEFAULT_ENV_PATH,
    _first_existing_env_path,
    _maybe_run_onboarding,
    _migrate_legacy_env,
)
from strategy_research.cli.onboard import (
    PROVIDERS,
    TIMEOUT_CHOICES,
    _finalize,
    _save_partial,
    is_onboarded,
    run_onboarding,
)


# ============================================================
# run_onboarding — 5-step wizard
# ============================================================


class TestRunOnboarding:
    """The prompt_toolkit-style wizard. Inputs are pre-canned via the
    test-mode ``inputs`` parameter."""

    def test_minimal_5_step_flow_writes_env(self, tmp_path: Path):
        """Inputs: OpenAI / gpt-4o / sk-test123 / 300 / (skip tushare)."""
        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test123", "300", ""],
            env_dir=tmp_path,
        )
        assert result == tmp_path / ".env"
        text = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in text
        assert "LANGCHAIN_MODEL_NAME=gpt-4o" in text
        assert "OPENAI_API_KEY=sk-test123" in text
        assert "OPENAI_BASE_URL=" in text
        assert "TIMEOUT_SECONDS=300" in text
        assert "MAX_RETRIES=2" in text
        assert "TUSHARE_TOKEN" not in text  # skipped on empty input

    def test_unknown_provider_raises_value_error(self, tmp_path: Path):
        """Step 1 fails cleanly when the user selects a non-existent
        provider label."""
        with pytest.raises(ValueError, match="provider not selected"):
            run_onboarding(inputs=["__not_a_provider__"], env_dir=tmp_path)

    def test_ollama_provider_skips_key_step(self, tmp_path: Path):
        """Ollama has ``key_env = None`` and ``base_env = None`` so the
        wizard must not prompt for an API key or set a base_url — only 4
        inputs suffice."""
        result = run_onboarding(
            inputs=["Ollama", "qwen2.5:32b", "300", ""],   # no key, 4 inputs
            env_dir=tmp_path,
        )
        text = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=ollama" in text
        assert "LANGCHAIN_MODEL_NAME=qwen2.5:32b" in text
        # Ollama has base_env=None → base_url never written to .env
        assert "OLLAMA_BASE_URL" not in text
        assert "API_KEY" not in text  # no key column populated

    def test_tushare_token_optional(self, tmp_path: Path):
        """User supplies a Tushare token → it ends up in .env."""
        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test", "300",
                    "tushare_token_xyz"],
            env_dir=tmp_path,
        )
        text = result.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in text

    def test_skip_tushare_short_circuits_final_step(self, tmp_path: Path):
        """When skip_tushare=True, only the first 4 inputs are consumed."""
        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test", "300"],
            skip_tushare=True,
            env_dir=tmp_path,
        )
        text = result.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN" not in text

    def test_no_inputs_raises_runtime_error_in_non_tty(self, tmp_path: Path):
        """``inputs=None`` is the live-TTY path; out-of-TTY callers must
        supply pre-canned inputs."""
        with pytest.raises(RuntimeError, match="needs `inputs`"):
            run_onboarding(inputs=None, env_dir=tmp_path)

    def test_exhausted_inputs_raises_runtime_error(self, tmp_path: Path):
        """If the wizard needs more inputs than supplied, fail loudly."""
        with pytest.raises(RuntimeError, match="ran out"):
            run_onboarding(inputs=[], env_dir=tmp_path)


# ============================================================
# is_onboarded
# ============================================================


class TestIsOnboarded:
    def test_false_when_no_env(self, tmp_path: Path):
        assert not is_onboarded(env_dir=tmp_path)

    def test_true_when_env_exists(self, tmp_path: Path):
        (tmp_path / ".env").write_text("LANGCHAIN_PROVIDER=openai\n")
        assert is_onboarded(env_dir=tmp_path)


# ============================================================
# _save_partial / _finalize (atomic write, chmod 0o600)
# ============================================================


class TestFileHelpers:
    def test_finalize_creates_env_atomic(self, tmp_path: Path):
        """After finalize: .env exists, .env.partial is gone."""
        values = {"LANGCHAIN_PROVIDER": "openai", "OPENAI_API_KEY": "sk-x"}
        path = _finalize(values, env_dir=tmp_path)
        assert path == tmp_path / ".env"
        assert path.exists()
        assert not (tmp_path / ".env.partial").exists()
        assert "LANGCHAIN_PROVIDER=openai" in path.read_text(encoding="utf-8")

    def test_save_partial_leaves_artifact(self, tmp_path: Path):
        """.env.partial stays until finalize clears it."""
        values = {"LANGCHAIN_PROVIDER": "openai"}
        _save_partial(values, env_dir=tmp_path)
        assert (tmp_path / ".env.partial").exists()
        assert "LANGCHAIN_PROVIDER=openai" in (
            tmp_path / ".env.partial"
        ).read_text(encoding="utf-8")

    def test_finalize_sets_mode_0o600_best_effort(self, tmp_path: Path):
        """On POSIX, _finalize should chmod the .env to 0600."""
        if sys.platform == "win32":
            pytest.skip("chmod semantics differ on Windows")
        values = {"LANGCHAIN_PROVIDER": "openai"}
        path = _finalize(values, env_dir=tmp_path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_finalize_overwrites_existing(self, tmp_path: Path):
        """_finalize replaces the existing .env atomically."""
        (tmp_path / ".env").write_text("OLD_KEY=old")
        values = {"LANGCHAIN_PROVIDER": "openai"}
        _finalize(values, env_dir=tmp_path)
        text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "OLD_KEY" not in text
        assert "LANGCHAIN_PROVIDER=openai" in text


# ============================================================
# _auto_onboard — 3-candidate probe + migration + auto-trigger
# ============================================================


class TestEnvProbe:
    def test_first_existing_returns_home_first(self, tmp_path: Path, monkeypatch):
        """If both HOME and cwd/.env exist, HOME wins."""
        # TMP/.env (rebranded by module-level import)
        (tmp_path / "home.env").write_text("LANGCHAIN_PROVIDER=openai")
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH",
            tmp_path / "home.env",
        )
        cwd_env = tmp_path / "cwd.env"
        cwd_env.write_text("LANGCHAIN_PROVIDER=anthropic")
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._CWD_ENV_PATH",
            cwd_env,
        )
        # project env should be lower priority
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._PROJECT_ENV_PATH",
            tmp_path / "missing.env",
        )
        assert _first_existing_env_path() == tmp_path / "home.env"

    def test_first_existing_returns_none_when_all_missing(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH",
            tmp_path / "missing1.env",
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._PROJECT_ENV_PATH",
            tmp_path / "missing2.env",
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._CWD_ENV_PATH",
            tmp_path / "missing3.env",
        )
        assert _first_existing_env_path() is None


class TestMigrateLegacyEnv:
    def test_copies_legacy_to_new_path(self, tmp_path: Path, monkeypatch):
        # Legacy path: <home>/.strategy-research/.env
        legacy_dir = tmp_path / ".strategy-research"
        legacy_dir.mkdir()
        legacy_file = legacy_dir / ".env"
        legacy_file.write_text(
            "LANGCHAIN_PROVIDER=openai\nOPENAI_API_KEY=sk-legacy\n"
        )

        # Make Path.home() return tmp_path so the function finds the
        # legacy file at tmp_path/.strategy-research/.env
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

        # Point the rebrand destination to a NEW dir so the copy target
        # doesn't already exist.
        new_dir = tmp_path / "new_env"
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_DIR", new_dir,
        )
        new_path = new_dir / ".env"
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH", new_path,
        )

        _migrate_legacy_env()

        assert new_path.exists(), "new .env was not created"
        assert new_path.read_text(encoding="utf-8") == (
            "LANGCHAIN_PROVIDER=openai\nOPENAI_API_KEY=sk-legacy\n"
        )
        # legacy NOT deleted (left for user to mv/rm)
        assert legacy_file.exists()

    def test_idempotent_when_new_already_exists(
        self, tmp_path: Path, monkeypatch
    ):
        """If new path already has content, leave it alone."""
        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / ".env").write_text("OLD=legacy")

        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_path = new_dir / ".env"
        new_path.write_text("NEW=current")

        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.Path.home", lambda: tmp_path,
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_DIR", new_dir,
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH", new_path,
        )

        _migrate_legacy_env()
        # New content untouched
        assert new_path.read_text(encoding="utf-8") == "NEW=current"

    def test_no_op_when_legacy_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.Path.home", lambda: tmp_path,
        )
        new_dir = tmp_path / "new"
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_DIR", new_dir,
        )
        new_path = new_dir / ".env"
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH", new_path,
        )
        _migrate_legacy_env()
        assert not new_path.exists()


class TestMaybeRunOnboarding:
    def test_skips_when_env_exists(self, tmp_path: Path, monkeypatch):
        """If HOME/.env exists, _maybe_run_onboarding returns True
        without consulting the wizard."""
        home_env = tmp_path / "home.env"
        home_env.write_text("LANGCHAIN_PROVIDER=openai")
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH",
            home_env,
        )
        # Even if the wizard would blow up, we shouldn't reach it.
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.run_onboarding",
            lambda **kw: pytest.fail("wizard was invoked"),
        )
        assert _maybe_run_onboarding(console=None) is True

    def test_returns_true_in_non_tty(self, tmp_path: Path, monkeypatch):
        """Non-TTY invocations must NOT prompt — return True so the
        caller falls through to whatever it would have done."""
        # Ensure no env candidate exists
        for attr in ("_DEFAULT_ENV_PATH", "_PROJECT_ENV_PATH", "_CWD_ENV_PATH"):
            monkeypatch.setattr(
                f"strategy_research.cli._auto_onboard.{attr}",
                tmp_path / f"nonexistent_{attr}.env",
            )
        # stdin is NOT a tty → _maybe_run_onboarding returns True
        # immediately without consulting the wizard.
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.sys.stdin",
            type("Mock", (), {"isatty": staticmethod(lambda: False)})(),
        )
        # Wizard must not be reached.
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.run_onboarding",
            lambda **kw: (_ for _ in ()).throw(
                AssertionError("wizard reached in non-TTY"),
            ),
        )
        assert _maybe_run_onboarding(console=None) is True

    def test_returns_false_when_wizard_returns_none(self, tmp_path: Path, monkeypatch):
        """On wizard CANCEL (run_onboarding returns None), return False
        so the CLI exits 0."""
        # Force _first_existing_env_path to return None so the wizard path
        # is actually reached.
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._first_existing_env_path",
            lambda: None,
        )
        # Pretend stdin+stdout are real TTYs
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.sys.stdin",
            type("Mock", (), {"isatty": staticmethod(lambda: True)})(),
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.sys.stdout",
            type("Mock", (), {"isatty": staticmethod(lambda: True)})(),
        )
        # Wizard returns None → user cancelled
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.run_onboarding",
            lambda **kw: None,
        )
        assert _maybe_run_onboarding(console=None) is False


# ============================================================
# cmd_run_onboarding — explicit CLI subcommand
# ============================================================


class TestCmdRunOnboarding:
    def test_help_describes_wizard(self, capsys):
        """The argparse help text must mention the credentials wizard."""
        from strategy_research.cli import main as cli_main
        with patch.object(sys, "argv", ["prog", "init", "--help"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "credentials wizard" in captured.out
        assert "vibe-trading" in captured.out

    def test_overwrite_existing_requires_force(self, tmp_path: Path, monkeypatch):
        """When .env exists and user declines Overwrite, return 0
        without changing the file."""
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        env_path = env_dir / ".env"
        env_path.write_text("ORIGINAL=keep_me\n")

        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_DIR", env_dir,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_PATH", env_path,
        )
        # Confirm.ask returns False
        with patch(
            "rich.prompt.Confirm.ask",
            return_value=False,
        ):
            from strategy_research.cli import cmd_run_onboarding
            args = argparse.Namespace(force=False)
            rc = cmd_run_onboarding(args)
        assert rc == 0
        assert env_path.read_text(encoding="utf-8") == "ORIGINAL=keep_me\n"

    def test_force_overwrites(self, tmp_path: Path, monkeypatch):
        """With --force, the wizard runs even if .env exists."""
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        env_path = env_dir / ".env"
        env_path.write_text("OLD=value\n")

        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_DIR", env_dir,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_PATH", env_path,
        )

        # Patch run_onboarding at the module level — cmd_run_onboarding
        # re-imports it on every call, so patching the module attribute
        # is sufficient.
        original_run = run_onboarding

        def fake_run_onboarding(*, console=None, env_dir=None, **_):
            # Use _DEFAULT_ENV_DIR as fallback when env_dir is not passed
            # (cmd_run_onboarding omits it from its call).
            from strategy_research.cli.onboard import _DEFAULT_ENV_DIR
            d = env_dir or _DEFAULT_ENV_DIR
            (d / ".env").write_text("NEW=fresh\n")
            return d / ".env"

        monkeypatch.setattr(
            "strategy_research.cli.onboard.run_onboarding",
            fake_run_onboarding,
        )
        from strategy_research.cli import cmd_run_onboarding
        args = argparse.Namespace(force=True)
        rc = cmd_run_onboarding(args)
        assert rc == 0
        assert "NEW=fresh" in env_path.read_text(encoding="utf-8")

    def test_wizard_cancel_returns_nonzero(self, tmp_path: Path, monkeypatch):
        """run_onboarding returns None on CANCEL → cmd returns 1."""
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        env_path = env_dir / ".env"
        env_path.unlink(missing_ok=True)

        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_DIR", env_dir,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard._DEFAULT_ENV_PATH", env_path,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard.run_onboarding",
            lambda **kw: None,
        )
        from strategy_research.cli import cmd_run_onboarding
        args = argparse.Namespace(force=False)
        rc = cmd_run_onboarding(args)
        assert rc == 1


# ============================================================
# TTY-mode: run_onboarding(inputs=None) with mocked selectors
# ============================================================


class TestRunOnboardingTTY:
    """Test the prompt_toolkit TTY branch of run_onboarding."""

    def test_tty_full_flow_openai(self, tmp_path: Path, monkeypatch):
        """Simulate full TTY flow: OpenAI → gpt-4o → key → 300s → skip tushare."""
        from strategy_research.cli.onboard import (
            _step_provider, _step_model, _step_key, _step_timeout, _step_tushare,
            _save_partial,
        )

        # Mock TTY detection
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        # Mock selectors to return predetermined values
        call_count = {"n": 0}

        def mock_select(prompt, choices, *, default_index=0):
            call_count["n"] += 1
            if call_count["n"] == 1:  # provider
                return "openai"
            if call_count["n"] == 2:  # model
                return "gpt-4o"
            if call_count["n"] == 5:  # tushare
                return "__skip__"
            return choices[0][0]  # default for timeout

        def mock_secret(prompt):
            return "sk-test1234567890"

        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back", mock_select,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard._prompt_secret", mock_secret,
        )

        # Mock stdin.isatty and stdout.isatty for run_onboarding
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        result = run_onboarding(env_dir=tmp_path)
        assert result is not None
        assert result == tmp_path / ".env"
        text = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in text
        assert "OPENAI_API_KEY=sk-test1234567890" in text

    def test_tty_cancel_returns_none(self, tmp_path: Path, monkeypatch):
        """CANCEL at step 1 → returns None."""
        from strategy_research.cli.onboard import CANCEL

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back",
            lambda *a, **kw: CANCEL,
        )

        result = run_onboarding(env_dir=tmp_path)
        assert result is None

    def test_tty_back_at_step0_returns_none(self, tmp_path: Path, monkeypatch):
        """BACK at step 0 → returns None (same as cancel)."""
        from strategy_research.cli.onboard import BACK

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back",
            lambda *a, **kw: BACK,
        )

        result = run_onboarding(env_dir=tmp_path)
        assert result is None

    def test_tty_back_goes_to_previous_step(self, tmp_path: Path, monkeypatch):
        """BACK at step 2 goes back to step 1, then select again."""
        from strategy_research.cli.onboard import BACK

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        # Track which step function is being called
        step_names = []
        step_calls = {"provider": 0, "model": 0, "timeout": 0, "tushare": 0}

        original_step_provider = None

        def mock_select(prompt, choices, *, default_index=0):
            # Detect which step by looking at the prompt text
            if "provider" in prompt.lower():
                step_names.append("provider")
                step_calls["provider"] += 1
                if step_calls["provider"] == 1:
                    return "openai"  # first time: select openai
                return "openai"  # after BACK: select openai again
            if "model" in prompt.lower():
                step_names.append("model")
                step_calls["model"] += 1
                if step_calls["model"] == 1:
                    return BACK  # first time: BACK
                return "gpt-4o"  # after BACK: select gpt-4o
            if "timeout" in prompt.lower():
                step_names.append("timeout")
                step_calls["timeout"] += 1
                return "300"
            if "tushare" in prompt.lower():
                step_names.append("tushare")
                step_calls["tushare"] += 1
                return "__skip__"
            return choices[0][0]

        def mock_secret(prompt):
            return "sk-test1234567890"

        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back", mock_select,
        )
        monkeypatch.setattr(
            "strategy_research.cli.onboard._prompt_secret", mock_secret,
        )

        result = run_onboarding(env_dir=tmp_path)
        assert result is not None
        text = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in text
        assert "OPENAI_API_KEY=sk-test1234567890" in text
        # Verify BACK happened: provider was called twice
        assert step_calls["provider"] == 2
        assert step_calls["model"] == 2

    def test_tty_ollama_skips_key_step(self, tmp_path: Path, monkeypatch):
        """Ollama has no key_env → _step_key prints hint and returns ok."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        step = {"n": 0}

        def mock_select(prompt, choices, *, default_index=0):
            step["n"] += 1
            if step["n"] == 1:
                return "ollama"  # provider
            if step["n"] == 2:
                return "qwen2.5:32b"  # model
            if step["n"] == 3:
                return "300"  # timeout
            if step["n"] == 4:
                return "__skip__"  # tushare
            return choices[0][0]

        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back", mock_select,
        )

        result = run_onboarding(env_dir=tmp_path)
        assert result is not None
        text = result.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=ollama" in text
        assert "API_KEY" not in text

    def test_tty_non_tty_raises_runtime_error(self, tmp_path: Path, monkeypatch):
        """Non-TTY input raises RuntimeError."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        with pytest.raises(RuntimeError, match="non-TTY"):
            run_onboarding(env_dir=tmp_path)


# ============================================================
# TTY helper functions
# ============================================================


class TestTTYHelpers:
    """Test the TTY helper functions."""

    def test_validate_key_valid(self):
        from strategy_research.cli.onboard import Provider, _validate_key
        p = Provider(
            "openai", "OpenAI", "GPT-4o", "gpt-4o",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "https://api.openai.com/v1", "sk-",
            ("gpt-4o",),
        )
        assert _validate_key(p, "sk-test1234567890") is None

    def test_validate_key_empty(self):
        from strategy_research.cli.onboard import Provider, _validate_key
        p = Provider(
            "openai", "OpenAI", "GPT-4o", "gpt-4o",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "https://api.openai.com/v1", "sk-",
            ("gpt-4o",),
        )
        err = _validate_key(p, "")
        assert "empty" in err.lower()

    def test_validate_key_wrong_prefix(self):
        from strategy_research.cli.onboard import Provider, _validate_key
        p = Provider(
            "openai", "OpenAI", "GPT-4o", "gpt-4o",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "https://api.openai.com/v1", "sk-",
            ("gpt-4o",),
        )
        err = _validate_key(p, "wrong-prefix-123456")
        assert "sk-" in err

    def test_validate_key_too_short(self):
        from strategy_research.cli.onboard import Provider, _validate_key
        p = Provider(
            "openai", "OpenAI", "GPT-4o", "gpt-4o",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "https://api.openai.com/v1", "sk-",
            ("gpt-4o",),
        )
        err = _validate_key(p, "sk-short")
        assert "short" in err.lower()
