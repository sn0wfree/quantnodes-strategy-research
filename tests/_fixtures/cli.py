"""CLI fixture helpers."""

from __future__ import annotations


def make_argv(*args: str) -> list[str]:
    """Build an argv list starting with the program name.

    >>> make_argv("session", "list")
    ['prog', 'session', 'list']
    """
    return ["prog", *args]


def make_argv_with_env(*args: str, env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Build argv + env for tests that need both."""
    return (["prog", *args], env)
