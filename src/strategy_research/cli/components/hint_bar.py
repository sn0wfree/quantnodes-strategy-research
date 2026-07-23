"""Hint bar — single-line bottom status bar with left + right aligned text.

Mirrors ``vibe-trading/cli/components/hint_bar.py``. The right hint
(current Ctrl+C semantics, etc.) stays visible; left truncates with an
ellipsis when the combined width overflows.
"""

from __future__ import annotations

import shutil
from typing import Optional

from rich.text import Text


def _resolve_width(width: Optional[int]) -> int:
    if width is not None and width > 0:
        return width
    try:
        cols = shutil.get_terminal_size().columns
        if cols > 0:
            return cols
    except (OSError, ValueError):
        pass
    return 80


def render_hint_bar(
    left: str,
    right: str = "",
    *,
    width: Optional[int] = None,
) -> Text:
    """Build a left-aligned ``left`` + right-aligned ``right`` hint bar.

    If the combined width exceeds ``width``, ``left`` is truncated with an
    ellipsis so ``right`` stays visible. ``width`` defaults to the terminal
    width (or 80 as fallback).
    """
    text = Text()
    width = _resolve_width(width)

    if not right:
        # Just left
        if len(left) > width:
            left = left[: max(0, width - 1)] + "…"
        text.append(left)
        return text

    # Reserve at least 1 space between left and right.
    if len(left) + len(right) + 1 > width:
        # Truncate left to fit
        max_left = max(0, width - len(right) - 2)  # space + ellipsis
        if max_left <= 0:
            text.append(right[:width])
            return text
        left = (left[: max_left - 1] + "…") if len(left) > max_left else left

    padding = max(1, width - len(left) - len(right))
    text.append(left, style="muted")
    text.append(" " * padding)
    text.append(right, style="primary.dim")
    return text


__all__ = ["render_hint_bar"]
