"""Step 1 acceptance: the RoundOutcome contract itself.

Pins the two properties that replace the implicit local-variable
convention between the langgraph / phases branches and the shared
finalization (see docs/step1-round-outcome-design.md §5):

1. Construction enforces completeness — a branch that omits a field
   fails loudly instead of leaving a NameError for the shared
   finalization to trip over later.
2. The field set is exactly the shared-consumption face — no silent
   additions/removals without a design change.
"""

from __future__ import annotations

import dataclasses

import pytest

from strategy_research.core.study.phase_engine import RoundOutcome

REQUIRED_FIELDS = {
    "metrics",
    "verdict",
    "eval_result",
    "agent_outputs",
    "strategist_output",
    "hypothesis",
    "predicted_affected",
    "captured_predictions",
    "backtest_error",
}


def _full_kwargs() -> dict:
    return {
        "metrics": {"calmar": 0.7},
        "verdict": "keep",
        "eval_result": {"decision": None, "aoa_llm_verdict": {}},
        "agent_outputs": {"researcher": {}},
        "strategist_output": {"action": "discover_local"},
        "hypothesis": "h",
        "predicted_affected": ["calmar"],
        "captured_predictions": {"calmar": 0.6},
        "backtest_error": None,
    }


def test_contract_field_set_matches_design():
    fields = {f.name for f in dataclasses.fields(RoundOutcome)}
    assert fields == REQUIRED_FIELDS


def test_no_defaults_so_omission_fails_at_construction():
    for f in dataclasses.fields(RoundOutcome):
        assert (
            f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        ), f"{f.name} must be required"


def test_omitting_one_field_raises_type_error():
    kwargs = _full_kwargs()
    del kwargs["captured_predictions"]
    with pytest.raises(TypeError):
        RoundOutcome(**kwargs)


def test_verdict_is_mutable_for_guidance_gate():
    """The shared gate rewrites verdict to 'discard' — the contract allows it."""
    outcome = RoundOutcome(**_full_kwargs())
    outcome.verdict = "discard"
    assert outcome.verdict == "discard"
