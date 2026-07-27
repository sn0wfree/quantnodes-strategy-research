"""Tests for core/llm/config_audit.py (C1-C5 detection + fix)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strategy_research.core.llm.config_audit import (
    AuditIssue,
    detect_issues,
    fix_issues,
    format_report,
    _read_llm_json,
    _read_env,
    _is_placeholder,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _write_llm(tmp_path: Path, llm: dict) -> Path:
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"llm": llm}))
    return p


def _write_env(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(content)
    return p


# ── _read_llm_json ───────────────────────────────────────────────────


class TestReadLlmJson:
    def test_valid_file(self, tmp_path):
        p = _write_llm(tmp_path, {"provider": "openai"})
        data = _read_llm_json(p)
        assert data["llm"]["provider"] == "openai"

    def test_missing_file(self, tmp_path):
        data = _read_llm_json(tmp_path / "nonexistent.json")
        assert data == {}

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text("{bad json")
        data = _read_llm_json(p)
        assert data == {}


# ── _read_env ────────────────────────────────────────────────────────


class TestReadEnv:
    def test_valid_file(self, tmp_path):
        p = _write_env(tmp_path, "KEY1=val1\nKEY2=val2\n")
        env = _read_env(p)
        assert env == {"KEY1": "val1", "KEY2": "val2"}

    def test_missing_file(self, tmp_path):
        env = _read_env(tmp_path / "nonexistent.env")
        assert env == {}

    def test_comments_and_blanks(self, tmp_path):
        p = _write_env(tmp_path, "# comment\n\nKEY=val\n")
        env = _read_env(p)
        assert env == {"KEY": "val"}


# ── _is_placeholder ──────────────────────────────────────────────────


class TestIsPlaceholder:
    def test_known_placeholders(self):
        assert _is_placeholder("sk-test")
        assert _is_placeholder("your-api-key-here")
        assert _is_placeholder("sk-placeholder")
        assert _is_placeholder("sk-YOUR-KEY")

    def test_short_sk(self):
        assert _is_placeholder("sk-abc")  # len < 20

    def test_real_key(self):
        assert not _is_placeholder("sk-cp-ZsXN5_lXSVVWspFgfyKdpmd_kBTKbT7tO5gZM6PjYPJw")

    def test_empty(self):
        assert not _is_placeholder("")


# ── detect_issues ────────────────────────────────────────────────────


class TestDetectIssues:
    def test_clean_config(self, tmp_path):
        """No issues → empty list."""
        llm = _write_llm(tmp_path, {
            "enabled": True,
            "provider": "minimax",
            "model": "minimax-M3",
            "api_key": "env:LLM_API_KEY",
            "base_url": "https://api.minimaxi.com/v1",
            "timeout": 300,
            "max_retries": 2,
        })
        env = _write_env(tmp_path, "LLM_API_KEY=sk-real\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        assert issues == []

    def test_c1_plaintext_key(self, tmp_path):
        llm = _write_llm(tmp_path, {
            "provider": "minimax",
            "model": "minimax-M3",
            "api_key": "sk-cp-1234567890abcdefghijklmnop",
        })
        env = _write_env(tmp_path, "")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C1" in codes

    def test_c2_placeholder_key(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "OPENAI_API_KEY=sk-test\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C2" in codes

    def test_c3_missing_llm_api_key(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C3" in codes

    def test_c4_missing_fields(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "model": "minimax-M3"})
        env = _write_env(tmp_path, "")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C4" in codes
        # Should detect base_url, timeout, max_retries
        c4_issues = [i for i in issues if i.code == "C4"]
        assert len(c4_issues) >= 3

    def test_c5_dead_keys(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "LANGCHAIN_PROVIDER=openai\nTIMEOUT_SECONDS=300\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C5" in codes

    def test_c5_openai_base_url_dead_when_not_openai(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "OPENAI_BASE_URL=https://api.openai.com/v1\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        assert "C5" in codes

    def test_c5_openai_base_url_alive_when_openai(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "openai", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "OPENAI_BASE_URL=https://api.openai.com/v1\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        codes = [i.code for i in issues]
        # OPENAI_BASE_URL should NOT be flagged when provider=openai
        assert "C5" not in codes

    def test_all_fixable_flags(self, tmp_path):
        """C1, C2, C4, C5 are fixable; C3 is not."""
        llm = _write_llm(tmp_path, {
            "provider": "minimax",
            "api_key": "sk-test-fake-placeholder-12345",
        })
        env = _write_env(tmp_path, "OPENAI_API_KEY=sk-test\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        for i in issues:
            if i.code == "C3":
                assert not i.fixable
            else:
                assert i.fixable, f"{i.code} should be fixable"


# ── fix_issues ───────────────────────────────────────────────────────


class TestFixIssues:
    def test_c1_fix(self, tmp_path):
        plaintext = "sk-cp-ZsXN5_lXSVVWspFgfyKdpmd_kBTKbT7tO5gZM6PjYPJw"
        llm = _write_llm(tmp_path, {
            "provider": "minimax",
            "api_key": plaintext,
        })
        env = _write_env(tmp_path, "")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        fix_issues(issues, llm_json_path=llm, env_path=env)

        # Verify llm.json
        new_llm = json.loads(llm.read_text())["llm"]
        assert new_llm["api_key"] == "env:LLM_API_KEY"

        # Verify .env
        new_env = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in env.read_text().splitlines() if "=" in l}
        assert new_env["LLM_API_KEY"] == plaintext

    def test_c2_fix(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "OPENAI_API_KEY=sk-test\nTUSHARE_TOKEN=abc\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        fix_issues(issues, llm_json_path=llm, env_path=env)

        new_env = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in env.read_text().splitlines() if "=" in l}
        assert "OPENAI_API_KEY" not in new_env
        assert new_env.get("TUSHARE_TOKEN") == "abc"

    def test_c4_fix(self, tmp_path):
        llm = _write_llm(tmp_path, {
            "provider": "minimax",
            "model": "minimax-M3",
            "api_key": "env:LLM_API_KEY",
        })
        env = _write_env(tmp_path, "")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        fix_issues(issues, llm_json_path=llm, env_path=env)

        new_llm = json.loads(llm.read_text())["llm"]
        assert new_llm["base_url"] == "https://api.minimaxi.com/v1"
        assert new_llm["timeout"] == 300
        assert new_llm["max_retries"] == 2

    def test_c5_fix(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "env:LLM_API_KEY"})
        env = _write_env(tmp_path, "LANGCHAIN_PROVIDER=openai\nTIMEOUT_SECONDS=300\nMAX_RETRIES=2\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        fix_issues(issues, llm_json_path=llm, env_path=env)

        new_env = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in env.read_text().splitlines() if "=" in l}
        assert "LANGCHAIN_PROVIDER" not in new_env
        assert "TIMEOUT_SECONDS" not in new_env
        assert "MAX_RETRIES" not in new_env

    def test_fix_populates_fix_summary(self, tmp_path):
        llm = _write_llm(tmp_path, {"provider": "minimax", "api_key": "sk-test-fake-placeholder"})
        env = _write_env(tmp_path, "OPENAI_API_KEY=sk-test\n")
        issues = detect_issues(llm_json_path=llm, env_path=env)
        fixed = fix_issues(issues, llm_json_path=llm, env_path=env)
        for i in fixed:
            if i.fixable:
                assert i.fix_summary, f"{i.code} should have fix_summary"


# ── format_report ────────────────────────────────────────────────────


class TestFormatReport:
    def test_empty(self):
        assert format_report([], use_color=False) == "  No issues found."

    def test_with_issues(self):
        issues = [AuditIssue("C1", "error", "test desc", True, "fixed")]
        report = format_report(issues, use_color=False)
        assert "C1" in report
        assert "test desc" in report
        assert "fixed" in report
