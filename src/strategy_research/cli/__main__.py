"""Allow running:

    python3 -m strategy_research.cli                # → Rich REPL
    python3 -m strategy_research.cli <subcommand>   # → argparse CLI

Or, after ``pip install -e .``, the ``quantnodes-research`` binary
inherits the same dispatch (see ``[project.scripts]`` in ``pyproject.toml``).

Detection rule: if argv is empty or every token is a REPL-only flag
(``--banner``), enter the Rich REPL. Otherwise forward to the legacy
argparse dispatcher in :func:`strategy_research.cli.main`.
"""
from __future__ import annotations

import sys
from typing import Sequence

from strategy_research.cli import main as _cli_main
from strategy_research.cli.interactive.main import main as _interactive_main

# Flags understood by the REPL entry point. Anything outside this set
# (subcommands, --help, --llm-list-profiles, ...) falls through to argparse.
_REPL_ONLY_FLAGS: frozenset[str] = frozenset({"--banner"})


def _should_enter_repl(argv: Sequence[str]) -> bool:
    """Return True iff argv routes to the Rich REPL rather than argparse."""
    if not argv:
        return True
    return all(tok in _REPL_ONLY_FLAGS for tok in argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level entry: bare / REPL-only flags → Rich REPL, else argparse."""
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if _should_enter_repl(argv):
        return _interactive_main(argv)
    # Argparse path: ``cli.main()`` calls ``parser.parse_args()`` which
    # reads ``sys.argv`` internally. Feed argv through by stubbing it
    # for that single call so existing code is untouched.
    original_argv = sys.argv
    sys.argv = [original_argv[0], *argv]
    try:
        return _cli_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
