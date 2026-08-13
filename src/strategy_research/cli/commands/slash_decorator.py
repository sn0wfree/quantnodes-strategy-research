"""Decorator for registering slash commands (Phase 2.3).

Before Phase 2.3, each slash command required:
1. A ``cmd_X(ctx, *args) -> int`` implementation function
2. A separate ``run_X(ctx, *args) -> int`` mirror function that forwarded
   to ``cmd_X`` — used by the slash-router entry point.

The ``run_X`` mirrors were duplicated boilerplate. Phase 2.3 introduces
the ``@slash_command`` decorator that:
1. Auto-registers the function in the slash_router's SLASH_COMMANDS table
2. Creates a ``run_X`` alias automatically so external callers can use it
3. Marks the command as a callable entry point

Public API
----------
    @slash_command("/model", description="...")
        Decorator. Adds the function to SLASH_COMMANDS and creates a
        ``run_<name>`` alias that delegates to the decorated function.

    get_run_function(name)
        Lookup helper for the auto-generated ``run_<name>`` aliases.

The decorator is backward-compatible: existing ``cmd_X`` functions can
co-exist with the decorator, and the slash_router continues to work.
"""

from __future__ import annotations

from typing import Any, Callable

from . import slash_router

# Tracks auto-generated run_<name> aliases for test introspection.
_RUN_ALIASES: dict[str, Callable[..., int]] = {}


def slash_command(
    name: str,
    *,
    description: str,
    handler_module: str | None = None,
) -> Callable[[Callable[..., int]], Callable[..., int]]:
    """Decorator that registers a slash command and generates its run_* alias.

    Args:
        name: Slash command name (without leading ``/``).
        description: Short description for the typeahead.
        handler_module: Optional module path stored in the registry.

    Returns:
        The original function (so it can still be called directly) plus
        a ``run_<name>`` alias is added to the module namespace.

    Example::

        @slash_command("/foo", description="Do foo")
        def cmd_foo(ctx, *args):
            return 0

        # Auto-generated alias:
        # def run_foo(ctx, *args):
        #     return cmd_foo(ctx, *args)
    """
    if not name.startswith("/"):
        raise ValueError(f"slash command name must start with '/': {name!r}")
    bare_name = name[1:]

    def decorator(handler_fn: Callable[..., int]) -> Callable[..., int]:
        # Build a Command entry for the slash-router registry.
        cmd = slash_router.Command(
            name=bare_name,
            description=description,
            handler_module=handler_module or handler_fn.__module__,
        )

        # Idempotent registration: if a Command with this name already
        # exists (e.g. loaded twice), replace it.
        existing = list(slash_router.SLASH_COMMANDS)
        new_list = tuple(c for c in existing if c.name != bare_name) + (cmd,)
        # Replace SLASH_COMMANDS via slice assignment (it's a tuple, but
        # slash_router re-exports the new value).
        slash_router.SLASH_COMMANDS = new_list

        # Generate the run_<name> alias and remember it.
        run_name = f"run_{bare_name}"

        def run_alias(ctx: Any = None, *args: Any) -> int:
            return handler_fn(ctx, *args)

        run_alias.__name__ = run_name
        run_alias.__qualname__ = run_name
        run_alias.__doc__ = f"Auto-generated alias for {name!r} command."

        # Attach the alias to the handler function's module namespace so
        # existing callers can `from module import run_X`.
        module = __import__(handler_fn.__module__, fromlist=["__name__"])
        setattr(module, run_name, run_alias)
        _RUN_ALIASES[run_name] = run_alias

        # Also return the alias from the decorator so callers can use it.
        return run_alias

    return decorator


def get_run_function(name: str) -> Callable[..., int] | None:
    """Look up an auto-generated ``run_<name>`` alias by command name.

    Args:
        name: Slash command name with or without leading ``/``.
    """
    bare = name.lstrip("/")
    return _RUN_ALIASES.get(f"run_{bare}")


def registered_run_aliases() -> list[str]:
    """Return all auto-generated ``run_<name>`` aliases (for tests)."""
    return sorted(_RUN_ALIASES)


def reset_run_aliases() -> None:
    """Clear all auto-generated aliases (for tests)."""
    _RUN_ALIASES.clear()


__all__ = [
    "get_run_function",
    "registered_run_aliases",
    "reset_run_aliases",
    "slash_command",
]
