"""Auto-trigger onboarding wizard on first-launch (TTY only).

Mirrors ``vibe-trading/cli/main.py:268 _maybe_run_onboarding``. Provides:

* :data:`_DEFAULT_ENV_PATH` — the canonical env file location
  (``~/.quantnodes/strategy_research/.env``).
* :data:`_PROJECT_ENV_PATH` — package install dir fallback
  (``<pkg_root>/.env``).
* :data:`_CWD_ENV_PATH` — current working directory fallback.
* :func:`_first_existing_env_path` — return the first candidate that
  actually exists, or ``None``.
* :func:`_migrate_legacy_env` — one-shot copy of
  ``~/.strategy-research/.env`` → ``~/.quantnodes/strategy_research/.env``;
  the legacy file is left in place for the user to inspect.
* :func:`_maybe_run_onboarding` — if no candidate ``.env`` exists and the
  session is interactive, run :func:`run_onboarding`. Returns ``True`` if
  startup should continue, ``False`` if the user cancelled.

Public API:

* :func:`_maybe_run_onboarding` — single entry point used by
  ``cli.interactive.main`` and (in the future) the binary's top-level
  ``main`` before any TTY prompt is shown.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from strategy_research.cli.onboard import (
    run_onboarding,
)

# Canonical env file (rebrand-aligned, see commit 1e87509).
from strategy_research.cli.onboard import _DEFAULT_ENV_DIR, _DEFAULT_ENV_PATH

# Two additional candidates per vibe-trading/cli/main.py:100-102.
_PROJECT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_CWD_ENV_PATH = Path.cwd() / ".env"


def _first_existing_env_path() -> Path | None:
    """Return the first ``.env`` candidate that exists, or ``None``.

    Order matches vibe-trading: ``HOME`` first (privacy-of-credentials
    preference), then project-local, then cwd.
    """
    for path in (_DEFAULT_ENV_PATH, _PROJECT_ENV_PATH, _CWD_ENV_PATH):
        if path.exists():
            return path
    return None


def _migrate_legacy_env() -> None:
    """Silently copy ``~/.strategy-research/.env`` to the new location.

    Idempotent. Skips when the legacy file is missing, when the new file
    already exists, or when the copy fails for any reason. Leaves the
    legacy file intact so the user can diff / mv / diff+rm at leisure.
    """
    legacy = Path.home() / ".strategy-research" / ".env"
    if not legacy.exists():
        return
    if _DEFAULT_ENV_PATH.exists():
        return  # newer config already wins; do not clobber
    try:
        _DEFAULT_ENV_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, _DEFAULT_ENV_PATH)
        try:
            _DEFAULT_ENV_PATH.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


def _maybe_run_onboarding(console) -> bool:
    """First-launch wizard — return True to continue, False on cancel.

    Tested by ``tests/test_auto_onboard.py``. Triggers only when **all**
    of the following hold:

    1. stdin+stdout are both TTYs (so prompt_toolkit can actually draw)
    2. No ``.env`` candidate exists in any of three locations

    The migration step runs unconditionally before the probe so legacy
    users (``~/.strategy-research/.env``) get a silent upgrade.
    """
    _migrate_legacy_env()

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return True  # non-TTY → let the user use --init manually
    if _first_existing_env_path() is not None:
        return True

    written = run_onboarding(console=console)
    if written is None:
        return False

    try:
        from dotenv import load_dotenv
        load_dotenv(written, override=True)
    except Exception:
        pass
    return True


__all__ = [
    "_DEFAULT_ENV_DIR",
    "_DEFAULT_ENV_PATH",
    "_PROJECT_ENV_PATH",
    "_CWD_ENV_PATH",
    "_first_existing_env_path",
    "_migrate_legacy_env",
    "_maybe_run_onboarding",
]
