"""Textual widget package for the strategy-research TUI.

Each widget is a thin Textual-native wrapper over existing CLI building
blocks (banner renderer, tool event formatter, etc.). The goal is that
the widgets expose a small ``update_*`` / ``write_*`` API and rely on
Rich ``Text`` / ``Renderable`` instances so the legacy code path stays
unchanged.
"""
from __future__ import annotations

from strategy_research.cli.tui.widgets.banner import Banner
from strategy_research.cli.tui.widgets.hint_footer import HintFooter
from strategy_research.cli.tui.widgets.input_bar import ChatInput
from strategy_research.cli.tui.widgets.rail import ActivityRail
from strategy_research.cli.tui.widgets.resume_dialog import ResumeOrNewModal
from strategy_research.cli.tui.widgets.sidebar import CommandSidebar
from strategy_research.cli.tui.widgets.transcript import TranscriptView

__all__ = [
    "Banner",
    "ChatInput",
    "CommandSidebar",
    "HintFooter",
    "ActivityRail",
    "ResumeOrNewModal",
    "TranscriptView",
]
