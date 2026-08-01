"""Backward-compat shim — ``AlphaZooAdapter`` has moved to ``core.alpha_zoo.loader``.

The old module was a 233-line duplicate of ``core.alpha_zoo.compute_alpha`` with
two extra methods (``compute_as_series``, ``compute_batch``) and a slightly
different validation policy.

Phase 1.2 of the refactor consolidates both into
``core.alpha_zoo.loader.AlphaLoader``. Existing callers continue to work:

    from strategy_research.core.alpha_zoo_adapter import AlphaZooAdapter  # OK

New code should prefer:

    from strategy_research.core.alpha_zoo import AlphaLoader, compute_alpha
"""

from .alpha_zoo.loader import AlphaLoader as AlphaZooAdapter

__all__ = ["AlphaZooAdapter"]