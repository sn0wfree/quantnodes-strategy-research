"""First-launch onboarding wizard.

Mirrors ``vibe-trading/cli/onboard.py``. Triggered when
``~/.quantnodes/strategy_research/.env`` is missing or when
``quantnodes-research init`` is run with no arguments.

Five back-steppable steps (provider → model → key → timeout → optional
Tushare for China A-share data). Each step persists immediately to
``.env.partial`` and is atomically renamed to ``.env`` on completion.

Public API:

* :data:`BACK`, :data:`CANCEL` — sentinel objects returned by selectors.
* :data:`PROVIDERS` — provider catalogue.
* :data:`TIMEOUT_CHOICES` — timeout preset offerings.
* :func:`run_onboarding` — full interactive flow.
* :func:`is_onboarded` — check whether ``.env`` already exists.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Sentinels for back-navigation / cancel returned by selectors.
BACK = object()
CANCEL = object()


_DEFAULT_ENV_DIR = Path.home() / ".quantnodes" / "strategy_research"
_DEFAULT_ENV_PATH = _DEFAULT_ENV_DIR / ".env"
_DEFAULT_PARTIAL_PATH = _DEFAULT_ENV_DIR / ".env.partial"


# ─── Provider catalogue ────────────────────────────────────────────────


@dataclass(frozen=True)
class Provider:
    """One selectable LLM provider option shown in step 1."""

    key: str
    label: str
    description: str
    default_model: str
    key_env: str | None
    base_env: str | None
    base_url: str
    key_prefix: str | None
    suggested_models: tuple[str, ...]


PROVIDERS: Final[tuple[Provider, ...]] = (
    Provider(
        "openai", "OpenAI", "GPT-4o direct",
        "gpt-4o",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "https://api.openai.com/v1", "sk-",
        ("gpt-4o", "gpt-4o-mini", "gpt-4.1"),
    ),
    Provider(
        "anthropic", "Anthropic", "Claude direct",
        "claude-3-5-sonnet-latest",
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
        "https://api.anthropic.com/v1", "sk-ant-",
        ("claude-3-5-sonnet-latest", "claude-3-opus-latest"),
    ),
    Provider(
        "openrouter", "OpenRouter", "200+ models via single API key",
        "deepseek/deepseek-chat",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1", "sk-or-",
        ("deepseek/deepseek-chat", "openai/gpt-4o", "anthropic/claude-3.5-sonnet"),
    ),
    Provider(
        "minimax", "MiniMax", "minimax provider",
        "MiniMax-M3",
        "MINIMAX_API_KEY", "MINIMAX_BASE_URL",
        "https://api.minimaxi.com/v1", "sk-",
        ("MiniMax-M3", "MiniMax-Text-01"),
    ),
    Provider(
        "ollama", "Ollama", "Local — free, no API key",
        "qwen2.5:32b",
        None, None,
        "http://localhost:11434", None,
        ("qwen2.5:32b", "llama3.3:70b", "deepseek-r1:14b"),
    ),
)


TIMEOUT_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("600", "600s (10 min — large backtests / swarm runs)"),
    ("300", "300s (5 min — normal autoresearch, recommended)"),
    ("120", "120s (2 min — quick lookup mode)"),
    ("60", "60s (1 min — smoke test only)"),
)


# ─── Filesystem helpers ────────────────────────────────────────────────


def _render_env(values: dict[str, str]) -> str:
    """Render values as a stable ``.env`` body (KEY=value lines)."""
    return "\n".join(
        f"{k}={v}" for k, v in values.items() if v
    ) + "\n"


def _save_partial(values: dict[str, str], *, env_dir: Path | None = None) -> None:
    """Best-effort write to ``.env.partial`` (crash-resilience nicety)."""
    env_dir = env_dir or _DEFAULT_ENV_DIR
    partial = env_dir / ".env.partial"
    try:
        env_dir.mkdir(parents=True, exist_ok=True)
        partial.write_text(_render_env(values), encoding="utf-8")
        try:
            partial.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


def _finalize(values: dict[str, str], *, env_dir: Path | None = None) -> Path:
    """Atomically write ``.env``. Returns the final path."""
    env_dir = env_dir or _DEFAULT_ENV_DIR
    env_dir.mkdir(parents=True, exist_ok=True)
    content = _render_env(values)
    fd, tmp_name = tempfile.mkstemp(prefix=".env.", dir=str(env_dir))
    final_path = env_dir / ".env"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, final_path)
        try:
            final_path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return final_path


def is_onboarded(*, env_dir: Path | None = None) -> bool:
    """True iff ``.env`` exists in the configured env_dir."""
    env_dir = env_dir or _DEFAULT_ENV_DIR
    return (env_dir / ".env").exists()


# ─── Public flow ──────────────────────────────────────────────────────


def run_onboarding(
    *,
    env_dir: Path | None = None,
    inputs: list[str] | None = None,
    skip_tushare: bool = False,
) -> Path:
    """Run the onboarding wizard.

    Args:
        env_dir: Override the env directory (used by tests).
        inputs: Optional pre-canned sequence of user inputs (used by tests).
        skip_tushare: If True, omit the optional Tushare step.

    Returns:
        Path of the final ``.env`` file.

    Each call is a single-shot 5-step flow. With ``inputs=None`` the
    implementation needs an interactive terminal (out of scope for CI).
    Tests pass ``inputs`` to drive the flow programmatically.
    """
    env_dir = env_dir or _DEFAULT_ENV_DIR
    values: dict[str, str] = {}

    def _next() -> str:
        if inputs is None:
            raise RuntimeError("run_onboarding needs `inputs` in non-TTY contexts")
        if not inputs:
            raise RuntimeError("ran out of onboarding inputs")
        return inputs.pop(0)

    # Step 1: provider (consume ONE input, look it up in the catalog).
    chosen = None
    first_choice = _next().strip()
    for p in PROVIDERS:
        if first_choice == p.label:
            values["LANGCHAIN_PROVIDER"] = p.key
            if p.base_env:
                values[p.base_env] = p.base_url
            chosen = p
            break
    if chosen is None:
        raise ValueError("provider not selected")

    # Step 2: model
    model = _next().strip() or chosen.default_model
    values["LANGCHAIN_MODEL_NAME"] = model

    # Step 3: API key (skip for providers with no key)
    if chosen.key_env:
        key = _next().strip()
        if key:
            values[chosen.key_env] = key

    # Step 4: timeout
    timeout = _next().strip() or "300"
    values["TIMEOUT_SECONDS"] = timeout
    values["MAX_RETRIES"] = "2"

    # Optional Step 5: Tushare (China A-share)
    if not skip_tushare:
        tushare = _next().strip()
        if tushare:
            values["TUSHARE_TOKEN"] = tushare

    return _finalize(values, env_dir=env_dir)


__all__ = [
    "BACK",
    "CANCEL",
    "Provider",
    "PROVIDERS",
    "TIMEOUT_CHOICES",
    "is_onboarded",
    "run_onboarding",
]
