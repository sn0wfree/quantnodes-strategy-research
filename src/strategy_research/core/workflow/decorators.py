"""Async decorators for Goal Workflow agent execution.

Four composable decorators applied to the ``AgentRunner.run`` callable:

  - ``with_retry(max_retries, delay_s)`` — retry on any exception
  - ``with_timeout(timeout_s)`` — raise asyncio.TimeoutError after N seconds
  - ``with_validation(validator)`` — validate output, retry if invalid
  - ``with_evidence_collection(collector, criterion_idx)`` — auto-collect
    agent output as goal evidence

Composition order (outermost first):
    run_func = with_retry(max_retries)(runner.run)
    run_func = with_timeout(timeout_s)(run_func)
    run_func = with_validation(validator)(run_func)
    run_func = with_evidence_collection(collector, idx)(run_func)
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def with_retry(
    max_retries: int,
    delay_s: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Retry the wrapped coroutine up to ``max_retries`` times on failure.

    Args:
        max_retries: Number of retries AFTER the first attempt.
        delay_s: Sleep between attempts.
        retry_on: Tuple of exception types that trigger a retry.
            asyncio.CancelledError is always re-raised.
    """
    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise  # never retry on cancellation
                except retry_on as exc:                   # noqa: PERF203
                    last_exc = exc
                    if attempt < max_retries:
                        logger.warning(
                            "%s attempt %d failed (%s), retrying...",
                            func.__qualname__, attempt + 1, exc,
                        )
                        await asyncio.sleep(delay_s)
            # All retries exhausted
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def with_timeout(timeout_s: float) -> Callable[
    [Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]
]:
    """Wrap a coroutine in ``asyncio.wait_for(timeout_s)``.

    Raises ``asyncio.TimeoutError`` when the budget is exceeded.
    Uses ``asyncio.wait_for`` for Python 3.10 compatibility
    (``asyncio.timeout`` context manager requires Python 3.11+).
    """
    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.wait_for(
                func(*args, **kwargs), timeout=timeout_s,
            )

        return wrapper

    return decorator


def with_validation(
    validator: Any,
    max_validation_attempts: int = 3,
) -> Callable[
    [Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]
]:
    """Validate the agent output; retry if invalid (up to N attempts).

    The validator must expose ``.validate(agent_name, output)`` returning
    an object with a ``.valid: bool`` attribute.
    """
    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            agent_id = args[0] if args else kwargs.get("agent_id", "unknown")
            result: Any = None
            for attempt in range(max_validation_attempts):
                result = await func(*args, **kwargs)
                vresult = validator.validate(agent_id, result)
                if getattr(vresult, "valid", True):
                    return result
                logger.warning(
                    "%s validation failed (attempt %d): %s",
                    agent_id, attempt + 1,
                    getattr(vresult, "errors", []),
                )
            return result  # accept the last attempt

        return wrapper

    return decorator


def with_evidence_collection(
    collector: Any,
    criterion_idx: int,
) -> Callable[
    [Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]
]:
    """Auto-collect agent output as goal evidence after successful run.

    The collector must expose ``.collect(agent_id, result, criterion_idx)``
    returning an int (number of evidence rows added).
    """
    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            agent_id = args[0] if args else kwargs.get("agent_id", "unknown")
            result = await func(*args, **kwargs)
            if result:
                try:
                    collector.collect(agent_id, result, criterion_idx)
                except Exception as exc:               # noqa: BLE001
                    logger.warning(
                        "Evidence collection failed for %s: %s", agent_id, exc,
                    )
            return result

        return wrapper

    return decorator


__all__ = [
    "with_retry",
    "with_timeout",
    "with_validation",
    "with_evidence_collection",
]