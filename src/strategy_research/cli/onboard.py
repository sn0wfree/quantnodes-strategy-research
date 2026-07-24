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


# ─── TTY selectors (prompt_toolkit) ──────────────────────────────────


def _select_with_back(
    prompt: str,
    choices: list[tuple[str, str]],
    *,
    default_index: int = 0,
) -> str | object:
    """Vertically-scrollable selector. Returns chosen value, BACK, or CANCEL.

    Keybindings: ↑/↓ navigate, Enter confirm, Esc/← back, Ctrl+C cancel.
    Falls back to a numeric stdin prompt if prompt_toolkit is unavailable.
    """
    from rich.console import Console
    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)

    try:
        from prompt_toolkit import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style as PTStyle
    except ImportError:
        return _select_numeric(choices, default_index)

    state = {"index": max(0, min(default_index, len(choices) - 1)), "result": None}

    def _format() -> FormattedText:
        out: list[tuple[str, str]] = []
        for i, (_, label) in enumerate(choices):
            if i == state["index"]:
                out.append(("class:cursor", "  > "))
                out.append(("class:selected", f"{label}\n"))
            else:
                out.append(("", "    "))
                out.append(("class:option", f"{label}\n"))
        out.append(
            ("class:hint", "\n  ↑/↓ navigate · Enter select · Esc/← back · Ctrl+C cancel")
        )
        return FormattedText(out)

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _(event):  # type: ignore[no-redef]
        state["index"] = (state["index"] - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    @kb.add("c-n")
    def _(event):  # type: ignore[no-redef]
        state["index"] = (state["index"] + 1) % len(choices)
        event.app.invalidate()

    @kb.add("enter")
    def _(event):  # type: ignore[no-redef]
        state["result"] = choices[state["index"]][0]
        event.app.exit()

    @kb.add("escape", eager=True)
    @kb.add("left")
    def _(event):  # type: ignore[no-redef]
        state["result"] = BACK
        event.app.exit()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):  # type: ignore[no-redef]
        state["result"] = CANCEL
        event.app.exit()

    brand_hex = Theme.brand_hex if hasattr(Theme, "brand_hex") else "258BFF"
    style = PTStyle.from_dict(
        {
            "cursor": f"#{brand_hex} bold",
            "selected": f"#{brand_hex} bold",
            "option": "",
            "hint": "#808080",
        }
    )
    layout = Layout(HSplit([Window(FormattedTextControl(_format), wrap_lines=False)]))
    app: Application = Application(
        layout=layout, key_bindings=kb, style=style, full_screen=False
    )
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    return state["result"] if state["result"] is not None else CANCEL


def _select_numeric(
    choices: list[tuple[str, str]], default_index: int
) -> str | object:
    """Stdin-only fallback selector (no BACK support)."""
    import sys

    for i, (_, label) in enumerate(choices, start=1):
        marker = ">" if (i - 1) == default_index else " "
        print(f"  {marker} [{i}] {label}")
    print("  (type number, q=cancel)")
    try:
        raw = sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    if raw in ("q", "quit", ""):
        return CANCEL
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    except ValueError:
        pass
    return CANCEL


def _prompt_secret(prompt: str) -> str | object:
    """Read masked input. Returns str, BACK, or CANCEL."""
    from rich.console import Console
    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)
    console.print(
        "  (input hidden · Enter to submit · Esc to go back · Ctrl+C to cancel)",
        style=Theme.muted,
    )
    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()
        sentinel: dict[str, object] = {"action": None}

        @kb.add("escape", eager=True)
        def _(event):  # type: ignore[no-redef]
            sentinel["action"] = BACK
            event.app.exit(result="")

        try:
            value = pt_prompt("> ", is_password=True, key_bindings=kb)
        except (EOFError, KeyboardInterrupt):
            return CANCEL
        if sentinel["action"] is BACK:
            return BACK
        return value.strip()
    except ImportError:
        import getpass

        try:
            return getpass.getpass("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return CANCEL


def _prompt_text(prompt: str, *, default: str = "") -> str | object:
    """Read plain text. Returns str, BACK, or CANCEL."""
    from rich.console import Console
    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)
    if default:
        console.print(
            f"  (Enter for default: {default} · Esc to go back)", style=Theme.muted
        )
    else:
        console.print("  (Enter to skip · Esc to go back)", style=Theme.muted)

    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()
        sentinel: dict[str, object] = {"action": None}

        @kb.add("escape", eager=True)
        def _(event):  # type: ignore[no-redef]
            sentinel["action"] = BACK
            event.app.exit(result="")

        try:
            value = pt_prompt("> ", key_bindings=kb)
        except (EOFError, KeyboardInterrupt):
            return CANCEL
        if sentinel["action"] is BACK:
            return BACK
        v = value.strip()
        return v if v else default
    except ImportError:
        try:
            raw = input("> ").strip()
            return raw if raw else default
        except (EOFError, KeyboardInterrupt):
            return CANCEL


def _validate_key(provider: Provider, key: str) -> str | None:
    """Return error message or None if key looks plausible."""
    if not key:
        return "API key cannot be empty."
    if provider.key_prefix and not key.startswith(provider.key_prefix):
        return f"Expected key to start with '{provider.key_prefix}'."
    if len(key) < 12:
        return "That key looks too short."
    return None


# ─── TTY step functions ──────────────────────────────────────────────


def _step_provider(
    values: dict[str, str], state: dict, skip_tushare: bool
) -> object:
    """Step 1: select LLM provider."""
    choices = [(p.key, f"{p.label:<14}  {p.description}") for p in PROVIDERS]
    result = _select_with_back("Pick a model provider", choices)
    if result in (BACK, CANCEL):
        return result
    provider = next(p for p in PROVIDERS if p.key == result)
    values["LANGCHAIN_PROVIDER"] = provider.key
    if provider.base_env:
        values[provider.base_env] = provider.base_url
    state["provider"] = provider
    _save_partial(values)
    return "ok"


def _step_model(
    values: dict[str, str], state: dict, skip_tushare: bool
) -> object:
    """Step 2: select model."""
    provider: Provider = state["provider"]
    choices: list[tuple[str, str]] = [
        (m, f"{m}{' (default)' if m == provider.default_model else ''}")
        for m in provider.suggested_models
    ]
    choices.append(("__custom__", "other (type custom model id)"))
    default_idx = next(
        (i for i, (v, _) in enumerate(choices) if v == provider.default_model), 0
    )
    result = _select_with_back("Pick a model", choices, default_index=default_idx)
    if result in (BACK, CANCEL):
        return result
    if result == "__custom__":
        custom = _prompt_text("Type the model id", default=provider.default_model)
        if custom in (BACK, CANCEL):
            return custom
        model = str(custom) or provider.default_model
    else:
        model = str(result)
    values["LANGCHAIN_MODEL_NAME"] = model
    _save_partial(values)
    return "ok"


def _step_key(
    values: dict[str, str], state: dict, skip_tushare: bool
) -> object:
    """Step 3: enter API key (skip for providers with no key)."""
    provider: Provider = state["provider"]
    if provider.key_env is None:
        from rich.console import Console
        from strategy_research.cli.theme import Theme

        msg = (
            "Ollama runs locally — no API key needed."
            if provider.key == "ollama"
            else "This provider does not require an API key."
        )
        Console().print(f"  {msg}", style=Theme.muted)
        return "ok"
    while True:
        key = _prompt_secret(
            f"Paste your {provider.label} API key "
            "(saved to ~/.quantnodes/strategy_research/.env, never logged)"
        )
        if key in (BACK, CANCEL):
            return key
        err = _validate_key(provider, str(key))
        if err is None:
            values[provider.key_env] = str(key)
            _save_partial(values)
            return "ok"
        from rich.console import Console
        from strategy_research.cli.theme import Theme

        Console().print(
            f"  {err}  Try again, or press Esc to go back.", style=Theme.danger
        )


def _step_timeout(
    values: dict[str, str], state: dict, skip_tushare: bool
) -> object:
    """Step 4: select request timeout."""
    choices = [(v, label) for v, label in TIMEOUT_CHOICES]
    result = _select_with_back("Default request timeout", choices, default_index=1)
    if result in (BACK, CANCEL):
        return result
    values["TIMEOUT_SECONDS"] = str(result)
    values["MAX_RETRIES"] = "2"
    _save_partial(values)
    return "ok"


def _step_tushare(
    values: dict[str, str], state: dict, skip_tushare: bool
) -> object:
    """Step 5: optional Tushare token (China A-share data)."""
    if skip_tushare:
        return "ok"
    choices = [
        ("__skip__", "No, skip (most users)"),
        ("__paste__", "Yes — paste my Tushare token"),
    ]
    result = _select_with_back(
        "Enable Tushare for China A-share data? (optional)", choices
    )
    if result in (BACK, CANCEL):
        return result
    if result == "__paste__":
        token = _prompt_secret("Tushare token")
        if token in (BACK, CANCEL):
            return token
        if str(token).strip():
            values["TUSHARE_TOKEN"] = str(token).strip()
            _save_partial(values)
    return "ok"


# ─── Public flow ──────────────────────────────────────────────────────


def run_onboarding(
    *,
    env_dir: Path | None = None,
    inputs: list[str] | None = None,
    skip_tushare: bool = False,
) -> Path | None:
    """Run the onboarding wizard.

    Two modes:

    * **Test mode** (``inputs`` provided): pops items from the list, no
      TTY interaction. Raises ``RuntimeError`` on empty list.
    * **TTY mode** (``inputs=None``): drives prompt_toolkit selectors
      with BACK/CANCEL support. Returns ``None`` when the user cancels.

    Args:
        env_dir: Override the env directory (used by tests).
        inputs: Optional pre-canned sequence of user inputs (used by tests).
        skip_tushare: If True, omit the optional Tushare step.

    Returns:
        Path of the final ``.env`` file, or ``None`` on cancel.
    """
    env_dir = env_dir or _DEFAULT_ENV_DIR

    # ─── Test-mode branch (existing, unchanged) ────────────────────────
    if inputs is not None:
        values: dict[str, str] = {}

        def _next() -> str:
            if not inputs:
                raise RuntimeError("ran out of onboarding inputs")
            return inputs.pop(0)

        # Step 1: provider
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

    # ─── TTY-mode branch (prompt_toolkit with BACK/CANCEL) ─────────────
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("run_onboarding needs `inputs` in non-TTY contexts")

    values = {}
    state: dict[str, object] = {"provider": None}
    steps = [
        _step_provider,
        _step_model,
        _step_key,
        _step_timeout,
        _step_tushare,
    ]
    i = 0
    while i < len(steps):
        result = steps[i](values, state, skip_tushare)
        if result is CANCEL:
            return None
        if result is BACK:
            if i == 0:
                return None  # back at step 0 = cancel
            i -= 1
            continue
        i += 1

    return _finalize(values, env_dir=env_dir)


__all__ = [
    "BACK",
    "CANCEL",
    "Provider",
    "PROVIDERS",
    "TIMEOUT_CHOICES",
    "_select_with_back",
    "_prompt_secret",
    "_prompt_text",
    "_validate_key",
    "_step_provider",
    "_step_model",
    "_step_key",
    "_step_timeout",
    "_step_tushare",
    "is_onboarded",
    "run_onboarding",
]
