"""Public re-exports for ``cli.commands``.

Mirrors ``vibe-trading/cli/commands/__init__.py``.
"""

from __future__ import annotations

from strategy_research.cli.commands.help import render_help_table
from strategy_research.cli.commands.show import (
    cmd_pine,
    cmd_show,
    cmd_skill,
)
from strategy_research.cli.commands.slash_router import (
    SLASH_COMMANDS,
    Command,
    find_exact,
    match_commands,
)
from strategy_research.cli.commands.slash_session import (
    cmd_export,
    cmd_history,
    cmd_search,
)

__all__ = [
    "SLASH_COMMANDS",
    "Command",
    "find_exact",
    "match_commands",
    "render_help_table",
    "cmd_export",
    "cmd_history",
    "cmd_search",
    "cmd_pine",
    "cmd_show",
    "cmd_skill",
]
