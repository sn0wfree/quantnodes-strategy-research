"""LLM configuration audit — detect and fix common issues.

Detects:
    C1: Plaintext API key in llm.json (should be ``env:VAR`` reference).
    C2: Placeholder ``OPENAI_API_KEY`` in .env (would shadow LLM_API_KEY).
    C3: Missing ``LLM_API_KEY`` in .env when llm.json references it.
    C4: Missing required fields in llm.json (base_url / timeout / max_retries).
    C5: Dead legacy keys in .env (LANGCHAIN_* / TIMEOUT_SECONDS / MAX_RETRIES /
        OPENAI_BASE_URL when provider != openai).
    C6: ``max_tokens`` is below the provider's recommended budget (long
        answers may be truncated at the cap).

Design:
    - Pure functions, no CLI dependencies.
    - Atomic writes (mkstemp + os.replace) + chmod 0600.
    - PROVIDER_DEFAULTS imported from config.py for C4/C6 fallback.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Final, Sequence

from .config import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_LLM_API_KEY,
    PROVIDER_DEFAULTS,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_PLACEHOLDER_KEYS: frozenset[str] = frozenset({
    "sk-test",
    "sk-placeholder",
    "your-api-key-here",
    "sk-YOUR-KEY",
})

_DEAD_ENV_KEYS: frozenset[str] = frozenset({
    "LANGCHAIN_PROVIDER",
    "LANGCHAIN_MODEL_NAME",
    "TIMEOUT_SECONDS",
    "MAX_RETRIES",
})

_REQUIRED_LLM_FIELDS: dict[str, Any] = {
    "base_url": None,      # filled from PROVIDER_DEFAULTS
    "timeout": 300,        # init wizard default
    "max_retries": 2,      # init wizard default
}


# ── Dataclass ────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class AuditIssue:
    """One detected configuration issue.

    Attributes:
        code:        Short code (C1–C5).
        severity:    "error" | "warn" | "info".
        description: Human-readable explanation.
        fixable:     Whether ``fix_issues`` can automatically resolve this.
        fix_summary: Brief text describing what fix was applied (populated
                     after ``fix_issues`` runs; empty string before).
    """
    code: str
    severity: str          # "error" | "warn" | "info"
    description: str
    fixable: bool
    fix_summary: str = ""  # filled after fix


# ── Detection ────────────────────────────────────────────────────────


def _read_llm_json(path: Path) -> dict[str, Any]:
    """Read llm.json, return ``{"llm": {...}}`` dict or ``{}``."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_env(path: Path) -> dict[str, str]:
    """Read .env into ``{KEY: value}`` dict (shared impl; empty kept)."""
    from ..utils.io_utils import read_dotenv
    return read_dotenv(path)


def _is_placeholder(value: str) -> bool:
    """Return True if the value looks like a test/placeholder key."""
    v = value.strip()
    if not v:
        return False
    if v in _PLACEHOLDER_KEYS:
        return True
    if len(v) < 20 and v.startswith("sk-"):
        return True  # real keys are always > 50 chars
    return False


# Canonical QuantNodes config locations (core-owned; api/cli/onboard
# import from here rather than the reverse — previously this module
# imported cli.onboard's private constants, forming a core↔cli cycle).
_QUANTNODES_DIR: Final[Path] = Path.home() / ".quantnodes"
DEFAULT_LLM_JSON_PATH: Final[Path] = _QUANTNODES_DIR / "llm.json"
DEFAULT_DOTENV_PATH: Final[Path] = _QUANTNODES_DIR / ".env"


def detect_issues(
    llm_json_path: Path | None = None,
    env_path: Path | None = None,
) -> list[AuditIssue]:
    """Detect configuration issues in llm.json and .env.

    Args:
        llm_json_path: Path to llm.json (default: ``~/.quantnodes/llm.json``).
        env_path:      Path to .env (default: ``~/.quantnodes/.env``).

    Returns:
        List of ``AuditIssue`` instances (may be empty if config is clean).
    """
    llm_path = llm_json_path or DEFAULT_LLM_JSON_PATH
    env_p = env_path or DEFAULT_DOTENV_PATH

    issues: list[AuditIssue] = []

    llm_data = _read_llm_json(llm_path)
    llm = llm_data.get("llm", {})
    env = _read_env(env_p)

    # ── C1: plaintext api_key in llm.json ──
    if isinstance(llm.get("api_key"), str) and llm["api_key"] and not llm["api_key"].startswith("env:"):
        issues.append(AuditIssue(
            code="C1",
            severity="error",
            description=(
                f"api_key in llm.json is plaintext ({llm['api_key'][:8]}...). "
                "Should be an env:LLM_API_KEY reference."
            ),
            fixable=True,
        ))

    # ── C2: placeholder OPENAI_API_KEY in .env ──
    openai_key = env.get(ENV_API_KEY, "")
    if openai_key and _is_placeholder(openai_key):
        issues.append(AuditIssue(
            code="C2",
            severity="warn",
            description=(
                f"OPENAI_API_KEY in .env is a placeholder ({openai_key[:12]}...). "
                "Will shadow LLM_API_KEY if left in place."
            ),
            fixable=True,
        ))

    # ── C3: missing LLM_API_KEY in .env when llm.json references it ──
    if llm.get("api_key", "").startswith("env:LLM_API_KEY") and not env.get(ENV_LLM_API_KEY):
        issues.append(AuditIssue(
            code="C3",
            severity="error",
            description=(
                "llm.json references env:LLM_API_KEY but LLM_API_KEY is not set in .env."
            ),
            fixable=False,
        ))

    # ── C4: missing required fields in llm.json ──
    for field, default_val in _REQUIRED_LLM_FIELDS.items():
        if field not in llm or llm[field] is None:
            if field == "base_url" and llm.get("provider"):
                provider = llm["provider"]
                defaults = PROVIDER_DEFAULTS.get(provider, {})
                fallback = defaults.get("base_url")
            else:
                fallback = default_val
            if fallback is not None:
                issues.append(AuditIssue(
                    code="C4",
                    severity="warn",
                    description=f"llm.json missing field '{field}' (will default to {fallback!r}).",
                    fixable=True,
                ))

    # ── C5: dead legacy keys in .env ──
    dead = [k for k in _DEAD_ENV_KEYS if k in env]
    # Special case: OPENAI_BASE_URL is dead only if provider != openai
    provider = llm.get("provider", "")
    openai_url = env.get(ENV_BASE_URL, "")
    if provider and provider != "openai" and openai_url:
        dead.append(ENV_BASE_URL)
    dead = list(dict.fromkeys(dead))  # dedup preserving order
    if dead:
        issues.append(AuditIssue(
            code="C5",
            severity="info",
            description=f"Legacy/dead keys in .env: {', '.join(dead)}",
            fixable=True,
        ))

    # ── C6: max_tokens below provider recommendation ──
    # Only fires if the user explicitly set a value that's lower than the
    # provider's recommended budget.  We don't flag the default 8192 (which
    # is set by the bridge fallback) because that's already a sane default.
    recommended = PROVIDER_DEFAULTS.get(provider, {}).get("max_tokens")
    user_max_tokens = llm.get("max_tokens")
    if (
        recommended
        and isinstance(user_max_tokens, int)
        and user_max_tokens > 0
        and user_max_tokens < recommended
    ):
        issues.append(AuditIssue(
            code="C6",
            severity="info",
            description=(
                f"max_tokens={user_max_tokens} is below the {provider} "
                f"recommendation ({recommended}).  Long answers may be "
                f"truncated at the cap."
            ),
            fixable=True,
        ))

    return issues


# ── Fix logic ────────────────────────────────────────────────────────


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write JSON file with chmod 0600 (shared impl)."""
    from ..utils.io_utils import atomic_write_json
    atomic_write_json(path, data)


def _atomic_write_env(path: Path, lines: dict[str, str]) -> None:
    """Atomic write .env file with chmod 0600 (shared impl).

    Args:
        lines: Ordered dict of key=value pairs. Empty-string values
               are written as ``KEY=`` (empty value, not omitted).
    """
    from ..utils.io_utils import atomic_write_env
    atomic_write_env(path, lines)


def fix_issues(
    issues: Sequence[AuditIssue],
    llm_json_path: Path | None = None,
    env_path: Path | None = None,
) -> list[AuditIssue]:
    """Apply fixes for detected issues. Returns issues with fix_summary filled.

    Args:
        issues:        Output of ``detect_issues()``.
        llm_json_path: Path to llm.json.
        env_path:      Path to .env.

    Returns:
        Updated issues list (same objects, fix_summary filled for applied fixes).
    """
    llm_path = llm_json_path or DEFAULT_LLM_JSON_PATH
    env_p = env_path or DEFAULT_DOTENV_PATH

    llm_data = _read_llm_json(llm_path)
    llm = llm_data.get("llm", {})
    env = _read_env(env_p)
    llm_changed = False
    env_changed = False
    updated: list[AuditIssue] = []

    for issue in issues:
        if not issue.fixable:
            updated.append(issue)
            continue

        # ── C1: plaintext key → env:LLM_API_KEY ──
        if issue.code == "C1":
            plaintext = llm.get("api_key", "")
            if plaintext and not plaintext.startswith("env:"):
                env[ENV_LLM_API_KEY] = plaintext
                env_changed = True
                llm["api_key"] = "env:LLM_API_KEY"
                llm_changed = True
                issue = dataclasses.replace(issue, fix_summary="Migrated plaintext key to env:LLM_API_KEY in .env")
            else:
                issue = dataclasses.replace(issue, fix_summary="(skipped: already env:VAR or empty)")

        # ── C2: placeholder OPENAI_API_KEY → delete ──
        elif issue.code == "C2":
            if ENV_API_KEY in env:
                del env[ENV_API_KEY]
                env_changed = True
                issue = dataclasses.replace(issue, fix_summary="Deleted OPENAI_API_KEY from .env")

        # ── C4: missing required fields → fill defaults ──
        elif issue.code == "C4":
            for field, default_val in _REQUIRED_LLM_FIELDS.items():
                if field not in llm or llm[field] is None:
                    if field == "base_url" and llm.get("provider"):
                        provider = llm["provider"]
                        defaults = PROVIDER_DEFAULTS.get(provider, {})
                        fallback = defaults.get("base_url", default_val)
                    else:
                        fallback = default_val
                    if fallback is not None:
                        llm[field] = fallback
                        llm_changed = True
            issue = dataclasses.replace(issue, fix_summary="Filled missing fields with defaults")

        # ── C5: dead legacy keys → delete ──
        elif issue.code == "C5":
            dead = [k for k in _DEAD_ENV_KEYS if k in env]
            provider = llm.get("provider", "")
            if provider and provider != "openai" and ENV_BASE_URL in env:
                dead.append(ENV_BASE_URL)
            removed = []
            for k in dead:
                if k in env:
                    del env[k]
                    removed.append(k)
                    env_changed = True
            issue = dataclasses.replace(issue, fix_summary=f"Deleted: {', '.join(removed)}")

        # ── C6: max_tokens below provider recommendation → bump up ──
        elif issue.code == "C6":
            provider = llm.get("provider", "")
            recommended = PROVIDER_DEFAULTS.get(provider, {}).get("max_tokens")
            if recommended:
                llm["max_tokens"] = recommended
                llm_changed = True
                issue = dataclasses.replace(
                    issue,
                    fix_summary=f"Set max_tokens to {recommended} (provider recommendation)",
                )

        updated.append(issue)

    # Write changes
    if llm_changed:
        _atomic_write_json(llm_path, llm_data)
        logger.info("config_audit: updated %s", llm_path)
    if env_changed:
        _atomic_write_env(env_p, env)
        logger.info("config_audit: updated %s", env_p)

    return updated


# ── Summary formatter ────────────────────────────────────────────────


def format_report(issues: list[AuditIssue], *, use_color: bool = True) -> str:
    """Format issues into a human-readable report string."""
    if not issues:
        return "  No issues found."
    lines = []
    for issue in issues:
        sev = issue.severity.upper()
        if use_color:
            sev_map = {"ERROR": "\033[91m", "WARN": "\033[93m", "INFO": "\033[96m"}
            reset = "\033[0m"
            color = sev_map.get(sev, "")
            sev_str = f"{color}{sev}{reset}"
        else:
            sev_str = sev
        fix = f" -> {issue.fix_summary}" if issue.fix_summary else ""
        lines.append(f"  [{issue.code}] {sev_str}: {issue.description}{fix}")
    return "\n".join(lines)
