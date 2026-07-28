"""Expression DSL for workflow branch conditions (P3.7).

Evaluates simple expressions like:
  ``factor_analyst.output.sharpe < 0.3``
  ``risk_controller.output.verdict == "fail"``
  ``data_quality.output.completeness > 0.8``

Supports:
  - Dot-path field access (agent_id.output.field)
  - Comparison operators: <, <=, >, >=, ==, !=
  - Numeric literals and string literals (quoted)
  - Boolean logic: and, or, not (future)

Usage:
    evaluator = ExpressionEvaluator(layer_results)
    result = evaluator.evaluate('factor_analyst.output.sharpe < 0.3')
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Comparison operators ──────────────────────────────────────

_COMPARATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

# Regex: <path> <op> <literal>
_EXPR_RE = re.compile(
    r"""
    ^\s*
    ([a-zA-Z_][\w.]*?)        # left: dot-separated path
    \s*
    (<=|>=|!=|==|<|>)          # operator
    \s*
    (".*?"|'.*?'|[0-9.+-]+)   # right: string or number literal
    \s*$
    """,
    re.VERBOSE,
)


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    """Resolve a dot-separated path like ``a.b.c`` in a nested dict."""
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _parse_literal(raw: str) -> Any:
    """Parse a literal value: number or quoted string."""
    # Quoted string
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    # Number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


class ExpressionEvaluator:
    """Evaluates simple comparison expressions against layer results."""

    def __init__(self, layer_results: dict[str, Any]) -> None:
        self._results = layer_results

    def evaluate(self, expression: str) -> bool:
        """Evaluate an expression string. Returns bool result.

        Raises:
            ValueError: If the expression cannot be parsed.
        """
        expr = expression.strip()

        # Handle simple boolean literals
        if expr.lower() in ("true", "1"):
            return True
        if expr.lower() in ("false", "0", ""):
            return False

        m = _EXPR_RE.match(expr)
        if m is None:
            raise ValueError(f"Cannot parse expression: {expression!r}")

        path, op, raw_value = m.group(1), m.group(2), m.group(3)
        expected = _parse_literal(raw_value)
        actual = _resolve_path(self._results, path)

        if actual is None:
            logger.debug("Path %r resolved to None in expression %r", path, expr)
            return False

        comparator = _COMPARATORS.get(op)
        if comparator is None:
            raise ValueError(f"Unknown operator: {op!r}")

        try:
            return comparator(actual, expected)
        except TypeError:
            logger.warning(
                "Type mismatch in %r: %r (%s) vs %r (%s)",
                expr, actual, type(actual).__name__,
                expected, type(expected).__name__,
            )
            return False


def evaluate_condition(
    expression: str,
    layer_results: dict[str, Any],
) -> bool:
    """Convenience function: evaluate a single expression."""
    evaluator = ExpressionEvaluator(layer_results)
    return evaluator.evaluate(expression)


__all__ = ["ExpressionEvaluator", "evaluate_condition"]