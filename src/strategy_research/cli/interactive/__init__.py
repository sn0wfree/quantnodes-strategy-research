"""Top-level public re-exports for the interactive REPL layer.

Mirrors the role of ``cli/intro.py`` in vibe-trading — a single import
target for the interactive-only API.
"""

from __future__ import annotations

from strategy_research.cli.interactive.completer import SlashCompleter

__all__ = ["SlashCompleter"]
