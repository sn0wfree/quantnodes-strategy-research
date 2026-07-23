"""Startup banner with ASCII logo and version line.

Mirrors ``vibe-trading/cli/ui/banner.py`` (8-line logo with per-character
gradient from `#258BFF` to `#A5CFFF`). Output is centred on the terminal.
"""

from __future__ import annotations

import shutil
from typing import Optional

from rich.console import Console
from rich.text import Text

# 8-line ASCII art for "STRATEGY-RESEARCH".
# Designed to fit an 80-col terminal; truncation logic in ``print_banner``
# keeps it usable on narrower consoles.
_LOGO_LINES = [
    " ____  _ _      ____                       _            ",
    "/ ___|| (_) ___|  _ \\  ___  _ __ ___   __ _(_)_   _____ ",
    "\\\\___ \\| | |/ _ \\ |_) |/ _ \\| '_ ` _ \\ / _` | \\ \\ / / _ \\",
    " ___) | | |  __/  _ <| (_) | | | | | | (_| | |\\ V /  __/",
    "|____/|_|_|\\___|_| \\_\\\\___/|_| |_| |_|\\__,_|_| \\_/ \\___|",
]

def _gradient_color(step: float) -> str:
    """Lerp between #258BFF (0.0) and #A5CFFF (1.0)."""
    def _hex(comp: int) -> str:
        return f"{comp:02x}"

    def _lerp(a: int, b: int) -> int:
        return round(a + (b - a) * step)

    r = _lerp(0x25, 0xA5)
    g = _lerp(0x8B, 0xCF)
    b = _lerp(0xFF, 0xFF)
    return f"#{_hex(r)}{_hex(g)}{_hex(b)}"


def _build_logo(width: int) -> Text:
    """Build the gradient logo, optionally truncated to fit the terminal."""
    usable = max(0, width - 2)  # leave 1 column padding each side
    text = Text()
    for line in _LOGO_LINES:
        truncated = line[:usable] if len(line) > usable else line
        for idx, ch in enumerate(truncated):
            if ch.strip():
                step = idx / max(1, len(truncated) - 1) if truncated else 0
                color = _gradient_color(step)
                text.append(ch, style=color)
            else:
                text.append(ch)
        text.append("\n")
    return text


def print_banner(
    console: Console,
    *,
    model: str = "unknown",
    version: str = "0.4.0",
    mode: str = "cli",
    width: Optional[int] = None,
) -> None:
    """Print the centered logo + version/header line."""
    if width is None:
        try:
            width = max(40, shutil.get_terminal_size().columns)
        except (OSError, ValueError):
            width = 80

    logo = _build_logo(width)
    console.print(logo, justify="center")

    header = Text()
    header.append("strategy-research", style="primary")
    header.append(f"  v{version}  ·  {mode}  ·  ", style="muted")
    header.append(model, style="primary.dim")
    console.print(header, justify="center")


__all__ = ["print_banner"]
