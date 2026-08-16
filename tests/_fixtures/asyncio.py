"""Async test helpers — small wrapper around ``asyncio.run``."""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """Run an awaitable in a fresh event loop and return the result.

    Convenience wrapper for synchronous test methods that need to exercise
    small bits of async code. Use pytest-asyncio for the majority of async
    tests — this is for the rare cases where a one-liner is clearer than a
    full async fixture.

    Example::

        def test_hook_dispatch():
            result = run_async(my_async_function(42))
            assert result == 84
    """
    return asyncio.run(coro)
