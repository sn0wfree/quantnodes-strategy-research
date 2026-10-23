from typing import Any
from rich.console import Console
from strategy_research.cli.theme import get_console

def _resolve_console(console: Any = None) -> Any:
    return console or get_console()

def cmd(*, console: Any = None) -> int:
    console = _resolve_console(console)
    console.print("x")
    return 0
