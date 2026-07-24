"""Public re-exports for the interactive REPL layer."""

from __future__ import annotations

from strategy_research.cli.interactive.completer import SlashCompleter
from strategy_research.cli.interactive.main import (
    InteractiveContext,
    dispatch_slash,
    main as interactive_main,
    process_turn,
)
from strategy_research.cli.onboard import (
    BACK,
    CANCEL,
    PROVIDERS,
    TIMEOUT_CHOICES,
    Provider,
    is_onboarded,
    run_onboarding,
)

__all__ = [
    "SlashCompleter",
    "InteractiveContext",
    "dispatch_slash",
    "interactive_main",
    "process_turn",
    "BACK",
    "CANCEL",
    "PROVIDERS",
    "TIMEOUT_CHOICES",
    "Provider",
    "is_onboarded",
    "run_onboarding",
]
