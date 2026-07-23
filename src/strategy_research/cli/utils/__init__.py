"""Public re-exports for ``cli.utils``.

Mirrors ``vibe-trading/cli/utils/__init__.py`` — flat re-export so callers
write ``from cli.utils import format_duration`` instead of going through the
submodule.
"""

from __future__ import annotations

from strategy_research.cli.utils.format import (
    abbreviate_num,
    format_duration,
    format_tokens,
)
from strategy_research.cli.utils.thinking_verbs import (
    THINKING_VERBS,
    pick_thinking_verb,
)

__all__ = [
    "abbreviate_num",
    "format_duration",
    "format_tokens",
    "THINKING_VERBS",
    "pick_thinking_verb",
]
