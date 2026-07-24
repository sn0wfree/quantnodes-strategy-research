"""E2E tests for quantnodes-research init via CLI entry point.

Tests the full path: CLI argparse → cmd_run_onboarding → run_onboarding
test-mode branch → _finalize → .env file on disk.

Selectors are bypassed by calling run_onboarding(inputs=...) directly,
which exercises the test-mode branch without needing a real TTY.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Helpers ─────────────────────────────────────────────────────────


def _write_fake_env(path: Path, content: str = "LANGCHAIN_PROVIDER=openai\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _patch_env(tmp_path: Path, monkeypatch):
    """Point all env-dir constants to tmp_path / 'env'."""
    from strategy_research.cli.onboard import _DEFAULT_ENV_DIR, _DEFAULT_ENV_PATH

    env_dir = tmp_path / "env"
    env_path = env_dir / ".env"

    monkeypatch.setattr(
        "strategy_research.cli.onboard._DEFAULT_ENV_DIR", env_dir
    )
    monkeypatch.setattr(
        "strategy_research.cli.onboard._DEFAULT_ENV_PATH", env_path
    )
    monkeypatch.setattr(
        "strategy_research.cli._auto_onboard._DEFAULT_ENV_DIR", env_dir
    )
    monkeypatch.setattr(
        "strategy_research.cli._auto_onboard._DEFAULT_ENV_PATH", env_path
    )
    return env_dir, env_path


def _run_init(monkeypatch, env_dir, env_path, args, inputs):
    """Run CLI init with test-mode inputs.

    Patches run_onboarding to call the test-mode branch (inputs-driven)
    so no TTY is needed.
    """
    from strategy_research.cli.onboard import run_onboarding as real_run

    def fake_run(**kw):
        return real_run(inputs=inputs, env_dir=env_dir)

    # Patch at the module level — cmd_run_onboarding re-imports it locally
    monkeypatch.setattr(
        "strategy_research.cli.onboard.run_onboarding", fake_run
    )

    with patch("sys.argv", ["prog", "init"] + args):
        from strategy_research.cli import main
        return main()


# ── E2E Tests ──────────────────────────────────────────────────────


class TestInitE2E:
    """E2E tests for quantnodes-research init via CLI entry point."""

    def test_force_overwrites_existing(self, tmp_path, monkeypatch):
        """--force 跳过确认，直接覆盖旧 .env。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)
        _write_fake_env(env_path, "OLD=value\n")

        rc = _run_init(
            monkeypatch, env_dir, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300", ""],
        )

        assert rc == 0
        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=openai" in content
        assert "OPENAI_API_KEY=sk-test1234567890" in content
        assert "OLD" not in content

    def test_new_user_creates_env(self, tmp_path, monkeypatch):
        """无 .env → 创建新文件 + chmod 0600。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, env_dir, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300", ""],
        )

        assert rc == 0
        assert env_path.exists()
        if os.name != "nt":
            mode = oct(env_path.stat().st_mode)[-3:]
            assert mode == "600"

    def test_cancel_returns_nonzero(self, tmp_path, monkeypatch):
        """Wizard CANCEL (empty inputs list) → 返回 1。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        # Empty inputs list → RuntimeError inside run_onboarding
        # but cmd_run_onboarding catches it... let's test with CANCEL sentinel
        from strategy_research.cli.onboard import CANCEL, run_onboarding as real_run

        def fake_cancel(**kw):
            return None  # simulates cancel

        monkeypatch.setattr(
            "strategy_research.cli.onboard.run_onboarding", fake_cancel
        )

        with patch("sys.argv", ["prog", "init", "--force"]):
            from strategy_research.cli import main
            rc = main()

        assert rc == 1
        assert not env_path.exists()

    def test_ollama_no_key(self, tmp_path, monkeypatch):
        """Ollama → 无 API_KEY 写入 .env。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, env_dir, env_path,
            args=["--force"],
            inputs=["Ollama", "qwen2.5:32b", "300", ""],
        )

        assert rc == 0
        content = env_path.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=ollama" in content
        assert "API_KEY" not in content

    def test_tushare_token_included(self, tmp_path, monkeypatch):
        """Paste Tushare token → .env 包含 TUSHARE_TOKEN。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, env_dir, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300",
                    "tushare_token_xyz"],
        )

        assert rc == 0
        content = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in content

    def test_help_shows_wizard_description(self, capsys):
        """--help 输出含 'credentials wizard'。"""
        with patch("sys.argv", ["prog", "init", "--help"]):
            from strategy_research.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "credentials wizard" in captured.out

    def test_auto_trigger_skips_when_env_exists(self, tmp_path, monkeypatch):
        """_maybe_run_onboarding: .env 已存在 → 跳过 wizard。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)
        _write_fake_env(env_path)

        def fail_if_called(*a, **kw):
            raise AssertionError("wizard called when env exists")

        monkeypatch.setattr(
            "strategy_research.cli.onboard._select_with_back", fail_if_called
        )

        from strategy_research.cli._auto_onboard import _maybe_run_onboarding
        from strategy_research.cli.theme import get_console

        assert _maybe_run_onboarding(get_console()) is True

    def test_auto_trigger_runs_when_missing(self, tmp_path, monkeypatch):
        """_maybe_run_onboarding: 无 .env + TTY → 调用 wizard。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._PROJECT_ENV_PATH",
            tmp_path / "nonexistent.env",
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard._CWD_ENV_PATH",
            tmp_path / "nonexistent2.env",
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.sys.stdin",
            type("M", (), {"isatty": staticmethod(lambda: True)})(),
        )
        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.sys.stdout",
            type("M", (), {"isatty": staticmethod(lambda: True)})(),
        )

        def fake_run_onboarding(**kw):
            env_dir.mkdir(parents=True, exist_ok=True)
            env_path.write_text("LANGCHAIN_PROVIDER=openai\n", encoding="utf-8")
            return env_path

        monkeypatch.setattr(
            "strategy_research.cli._auto_onboard.run_onboarding",
            fake_run_onboarding,
        )

        from strategy_research.cli._auto_onboard import _maybe_run_onboarding
        from strategy_research.cli.theme import get_console

        assert _maybe_run_onboarding(get_console()) is True
        assert env_path.exists()

    def test_existing_env_not_overwritten_without_force(self, tmp_path, monkeypatch):
        """无 --force + .env 已存在 + 用户拒绝 → 原文件不变。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)
        _write_fake_env(env_path, "ORIGINAL=keep_me\n")

        with patch("rich.prompt.Confirm.ask", return_value=False):
            with patch("sys.argv", ["prog", "init"]):
                from strategy_research.cli import main
                rc = main()

        assert rc == 0
        assert env_path.read_text(encoding="utf-8") == "ORIGINAL=keep_me\n"

    def test_validate_key_unit(self):
        """_validate_key: prefix / length / empty checks."""
        from strategy_research.cli.onboard import Provider, _validate_key

        p = Provider(
            "openai", "OpenAI", "GPT-4o", "gpt-4o",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "https://api.openai.com/v1", "sk-",
            ("gpt-4o",),
        )
        # Wrong prefix
        err = _validate_key(p, "wrong-1234567890")
        assert "sk-" in err
        # Too short
        err = _validate_key(p, "sk-short")
        assert "short" in err.lower()
        # Empty
        err = _validate_key(p, "")
        assert "empty" in err.lower()
        # Valid
        assert _validate_key(p, "sk-test1234567890") is None

    def test_anthropic_provider(self, tmp_path, monkeypatch):
        """Anthropic → correct key_env + base_url。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, env_dir, env_path,
            args=["--force"],
            inputs=["Anthropic", "claude-3-5-sonnet-latest",
                    "sk-ant-test1234567890", "300", ""],
        )

        assert rc == 0
        content = env_path.read_text(encoding="utf-8")
        assert "LANGCHAIN_PROVIDER=anthropic" in content
        assert "ANTHROPIC_API_KEY=sk-ant-test1234567890" in content
        assert "ANTHROPIC_BASE_URL=https://api.anthropic.com/v1" in content

    def test_skip_tushare(self, tmp_path, monkeypatch):
        """skip_tushare=True → TUSHARE_TOKEN 不在 .env。"""
        env_dir, env_path = _patch_env(tmp_path, monkeypatch)

        from strategy_research.cli.onboard import run_onboarding

        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300"],
            skip_tushare=True,
            env_dir=env_dir,
        )

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN" not in content
