"""Tests for the prompt schema extractor.

Covers:
- Fenced vs raw JSON block extraction
- Section detection (## 输出 / ## 输出格式 / ## 输出 Schema)
- # @label / @type / @core / @enum hint parsing
- Field type inference from sample values
- Core field heuristic (decisional = core)
- Per-agent schema validation (all 8 main agents)
- Malformed JSON / missing prompt → graceful fallback
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.prompt_schema_extractor import (
    AgentSchema,
    FieldHints,
    extract_agent_schema,
    load_all_schemas,
    _extract_json_blocks,
    _find_output_section,
    _parse_hints_from_line,
    _infer_type,
    _is_core_field,
    _find_balanced_end,
)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src" / "strategy_research" / "templates" / ".prompts"


# ── Section detection ─────────────────────────────────────────────────

class TestFindOutputSection:
    def test_detects_output_heading(self):
        md = "# Title\n\n## 输出\n\nSome text\n\n## Rules\n\nMore text"
        start, end = _find_output_section(md)
        assert start >= 0
        assert md[start:start+4] == "## 输"

    def test_detects_output_format_heading(self):
        md = "## 输出格式\n\n```json\n{}\n```"
        start, end = _find_output_section(md)
        assert start >= 0

    def test_returns_neg1_when_no_output_section(self):
        md = "# Title\n\n## Input\n\nSome text"
        start, end = _find_output_section(md)
        assert start == -1

    def test_find_balanced_end_simple(self):
        text = '{"a": 1}'
        assert _find_balanced_end(text, 0) == 7

    def test_find_balanced_end_nested(self):
        text = '{"a": {"b": 2}, "c": 3}'
        assert _find_balanced_end(text, 0) == len(text) - 1

    def test_find_balanced_end_string_with_braces(self):
        text = '{"a": "x{y}z"}'
        assert _find_balanced_end(text, 0) == len(text) - 1

    def test_find_balanced_end_unbalanced(self):
        text = '{"a": {"b": 2}'
        assert _find_balanced_end(text, 0) == -1


# ── JSON block extraction ─────────────────────────────────────────────

class TestExtractJsonBlocks:
    def test_fenced_json_block(self):
        md = 'Some text\n```json\n{"key": "val"}\n```\nMore text'
        blocks = _extract_json_blocks(md)
        assert len(blocks) == 1
        assert json.loads(blocks[0]) == {"key": "val"}

    def test_raw_json_block(self):
        md = 'Output:\n\n{"action": "test"}\n\n## Rules'
        blocks = _extract_json_blocks(md)
        assert len(blocks) == 1
        assert json.loads(blocks[0]) == {"action": "test"}

    def test_fenced_block_with_empty_lang(self):
        md = '```\n{"action": "test"}\n```'
        blocks = _extract_json_blocks(md)
        assert len(blocks) == 1

    def test_no_json_returns_empty(self):
        md = 'No JSON here, just text'
        blocks = _extract_json_blocks(md)
        assert len(blocks) == 0


# ── Hint parsing ───────────────────────────────────────────────────────

class TestParseHints:
    def test_label(self):
        hints = _parse_hints_from_line('# @label: 是否通过')
        assert hints["label"] == "是否通过"

    def test_multiple_hints(self):
        hints = _parse_hints_from_line('# @label: 风险评级 @type: enum @core: true')
        assert hints["label"] == "风险评级"
        assert hints["type"] == "enum"
        assert hints["core"] == "true"

    def test_enum_json(self):
        hints = _parse_hints_from_line('# @enum: {"Green":"🟢绿","Red":"🔴红"}')
        assert "enum" in hints
        assert json.loads(hints["enum"]) == {"Green": "🟢绿", "Red": "🔴红"}

    def test_no_hints_on_normal_line(self):
        hints = _parse_hints_from_line('"risk_passed": true,')
        assert hints == {}

    def test_empty_line(self):
        hints = _parse_hints_from_line("")
        assert hints == {}


# ── Field type inference ───────────────────────────────────────────────

class TestInferType:
    def test_bool(self):
        assert _infer_type(True) == "bool"
        assert _infer_type(False) == "bool"

    def test_number(self):
        assert _infer_type(42) == "number"
        assert _infer_type(3.14) == "number"

    def test_string(self):
        assert _infer_type("hello") == "string"

    def test_enum_string(self):
        assert _infer_type("Green | Yellow | Red") == "enum"

    def test_array(self):
        assert _infer_type([]) == "array"
        assert _infer_type(["a", "b"]) == "array"
        assert _infer_type([1, 2]) == "array"

    def test_object(self):
        assert _infer_type({"a": 1}) == "object"

    def test_none(self):
        assert _infer_type(None) == "string"


# ── Core field heuristic ───────────────────────────────────────────────

class TestCoreField:
    def test_action_is_core(self):
        assert _is_core_field("action", FieldHints()) is True

    def test_verdict_is_core(self):
        assert _is_core_field("verdict", FieldHints()) is True

    def test_hypothesis_is_core(self):
        assert _is_core_field("hypothesis", FieldHints()) is True

    def test_weights_is_not_core(self):
        assert _is_core_field("weights", FieldHints()) is False

    def test_var_95_is_not_core(self):
        assert _is_core_field("var_95", FieldHints()) is False

    def test_explicit_core_overrides_heuristic(self):
        assert _is_core_field("weights", FieldHints(core=True)) is True
        assert _is_core_field("action", FieldHints(core=False)) is False


# ── Per-agent schema validation ────────────────────────────────────────

class TestExtractAgentSchemas:
    """Validate schema extraction for each main agent."""

    @pytest.mark.parametrize("role", [
        "researcher", "risk_controller", "portfolio_construction",
        "anti_overfit_analyst", "attribution_analyst", "strategist",
    ])
    def test_extract_returns_schema(self, role):
        schema = extract_agent_schema(role, PROMPTS_DIR)
        assert schema is not None, f"Expected schema for {role}"
        assert schema.role == role
        assert len(schema.fields) > 0

    def test_researcher_has_action_field(self):
        schema = extract_agent_schema("researcher", PROMPTS_DIR)
        assert schema.action_field == "action"
        assert "optimize_param" in (schema.action_enum or [])

    def test_risk_controller_core_fields(self):
        schema = extract_agent_schema("risk_controller", PROMPTS_DIR)
        core = [k for k, v in schema.field_hints.items() if v.core]
        # From the prompt's JSON example: risk_passed / risk_rating are
        # decisional (core); var_95 / stress_results are detail (non-core).
        assert "risk_passed" in core
        assert "risk_rating" in core
        assert "var_95" not in core
        assert "stress_results" not in core

    def test_risk_controller_enum_labels(self):
        schema = extract_agent_schema("risk_controller", PROMPTS_DIR)
        hints = schema.field_hints["risk_rating"]
        assert hints.enum_values is not None
        assert "Green" in hints.enum_values
        assert "Red" in hints.enum_values

    def test_portfolio_construction_weights_type(self):
        schema = extract_agent_schema("portfolio_construction", PROMPTS_DIR)
        hints = schema.field_hints["weights"]
        assert hints.type == "object"

    def test_anti_overfit_verdict_enum(self):
        schema = extract_agent_schema("anti_overfit_analyst", PROMPTS_DIR)
        hints = schema.field_hints["verdict"]
        assert hints.type in ("enum", "string")
        assert hints.core is True

    def test_missing_prompt_returns_none(self):
        schema = extract_agent_schema("nonexistent_role", PROMPTS_DIR)
        assert schema is None

    def test_load_all_schemas(self):
        schemas = load_all_schemas(PROMPTS_DIR)
        assert "researcher" in schemas
        assert "risk_controller" in schemas
        assert len(schemas) >= 6  # at least the 6 main agents
