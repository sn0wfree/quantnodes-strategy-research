"""Top-level entry-point for the QuantNodes-Research CLI.

Invocation matrix (mirrors vibe-trading):

================================  ============================================
``quantnodes-research`` (TTY)       Full-screen Textual TUI (Commit 2+).
``quantnodes-research`` (non-TTY)   argparse help printed + exit 0.
``quantnodes-research --banner``    legacy prompt_toolkit REPL.
``quantnodes-research --repl``      legacy prompt_toolkit REPL.
``quantnodes-research <subcmd>``    legacy argparse dispatcher.
``quantnodes-research resume <id>``  legacy REPL with that session loaded
                                    (currently a no-op stub; Commit 5).
================================  ============================================

TTY guard
---------
When the invocation qualifies for TUI but ``sys.stdin.isatty()`` or
``sys.stdout.isatty()`` is False (piped invocation, CI runner, etc.) we
deliberately fall through to ``cli.main``'s argparse help so we don't
hang waiting for a TTY.

The legacy ``cli.main`` argparse path remains authoritative for every
non-bare invocation: ``status``, ``autoresearch``, ``goal``,
``hypothesis``, ``validate``, ``export``, ``mcp``, ``api``, ``webui``,
etc. — all 19 subcommands the v0.4.0 release ships with.

Test injection
--------------
``_is_interactive_invocation`` accepts an optional ``is_tty`` callable.
Tests pass a deterministic stub rather than relying on monkeypatched
``sys.stdin``/``sys.stdout`` (which conflict with pytest's own capture).
"""
from __future__ import annotations

import sys
from typing import Callable, Sequence

from strategy_research.cli import main as _cli_main
from strategy_research.cli.interactive.main import main as _interactive_main
from strategy_research.cli.tui.app import ResearchApp


# Flags understood by the REPL entry point only. Anything outside this
# set (subcommands, --help, --llm-list-profiles, ...) falls through to
# argparse.
_REPL_ONLY_FLAGS: frozenset[str] = frozenset({"--banner", "--repl"})

# Flags the TUI entry-point recognises. Anything outside this set at a
# bare invocation is forwarded to argparse.
_TUI_ONLY_FLAGS: frozenset[str] = frozenset({"--banner"})


def _default_is_tty() -> bool:
    """Default TTY probe — both streams must be real TTYs."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _is_interactive_invocation(
    argv: Sequence[str],
    *,
    is_tty: Callable[[], bool] = _default_is_tty,
) -> bool:
    """Return ``True`` iff this argv routes to the interactive surface.

    Interactive entry requires:

    * No subcommand (empty argv or every token is a recognised flag).
    * TTY for both stdin and stdout (a piped invocation would hang
      waiting for a TTY). Tests pass an ``is_tty`` stub to bypass the
      real ``sys.stdin`` / ``sys.stdout`` probe.

    Args:
        argv: The argument list passed to :func:`main`, *excluding*
            ``sys.argv[0]``.
        is_tty: Callable returning ``True`` iff the runtime has TTY I/O.
            Defaults to the ``sys.stdin``/``sys.stdout`` probe.

    Returns:
        ``True`` iff the caller should drive the interactive surface.
    """
    if not is_tty():
        return False

    if not argv:
        return True
    if argv[0].startswith("-"):
        if argv[0] in _REPL_ONLY_FLAGS or argv[0] in _TUI_ONLY_FLAGS:
            return True
        return False
    return False


def _wants_legacy_repl(argv: Sequence[str]) -> bool:
    """``--repl`` and ``--banner`` (legacy REPL) opt out of TUI."""
    return bool(argv) and argv[0] in _REPL_ONLY_FLAGS


def main(
    argv: Sequence[str] | None = None,
    *,
    is_tty: Callable[[], bool] = _default_is_tty,
) -> int:
    """Top-level entry: bare / flag-only → TUI or REPL, else argparse.

    Dispatch table:

    * TTY + bare → Textual TUI (``ResearchApp().run()``).
    * TTY + ``--repl`` → legacy prompt_toolkit REPL.
    * TTY + ``--banner`` → legacy prompt_toolkit REPL with banner
      pre-rendered (alias kept for back-compat with v0.3.x).
    * non-TTY → delegate to argparse.
    * subcommand / unknown flag → delegate to argparse.

    Args:
        argv: The argument list (defaults to ``sys.argv[1:]``).
        is_tty: Test-injection hook; defaults to a real TTY probe.

    Returns:
        Process exit code.
    """
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # TUI dispatch (preferred) — full-screen interactive.
    if _is_interactive_invocation(
        raw_argv, is_tty=is_tty
    ) and not _wants_legacy_repl(raw_argv):
        try:
            app = ResearchApp()
            return app.run() or 0
        except SystemExit as exc:
            return int(exc.code or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"TUI failed: {exc}\nFalling back to legacy REPL.")
            return _interactive_main(raw_argv)

    # Legacy REPL dispatch (opt-in).
    if raw_argv and raw_argv[0] in _REPL_ONLY_FLAGS:
        return _interactive_main(raw_argv)

    # Subcommand / non-TTY: argparse CLI path.
    return _run_argparse(raw_argv)


def _run_argparse(argv: Sequence[str]) -> int:
    """Run the legacy argparse dispatcher in :func:`strategy_research.cli.main`.

    Stubs ``sys.argv`` so the legacy :func:`cli.main` (which uses
    ``argparse.ArgumentParser.parse_args()`` with the default ``sys.argv``
    binding) sees our ``argv`` instead of the real process argv.
    """
    original_argv = sys.argv
    sys.argv = [original_argv[0], *argv] if argv else [original_argv[0]]
    try:
        try:
            return int(_cli_main())
        except SystemExit as exc:
            return int(exc.code or 0)
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
