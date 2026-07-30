"""Tests for the shared tool utilities (utils.py).

Covers:
- safe_get_param: standard values, type coercion, unwrapping,
  JSON stringification, missing keys
- try_unwrap_list / try_unwrap_dict: explicit unwrap
- err_actionable: structured error payloads
- truncate: long values
"""
from __future__ import annotations

import json

import pytest

from strategy_research.core.agent.builtin_tools.utils import (
    err_actionable,
    safe_get_param,
    truncate,
    try_unwrap_dict,
    try_unwrap_list,
)


# ── safe_get_param: standard reads ────────────────────────────


class TestSafeGetParamStandard:
    def test_missing_returns_default(self):
        assert safe_get_param({}, "x", str, default="hi") == "hi"

    def test_none_returns_default(self):
        assert safe_get_param({"x": None}, "x", str, default="hi") == "hi"

    def test_existing_str(self):
        assert safe_get_param({"x": "hi"}, "x", str) == "hi"

    def test_existing_int(self):
        assert safe_get_param({"x": 42}, "x", int) == 42

    def test_existing_list(self):
        assert safe_get_param({"x": [1, 2, 3]}, "x", list) == [1, 2, 3]

    def test_existing_dict(self):
        d = {"k": "v"}
        assert safe_get_param({"x": d}, "x", dict) == d


# ── safe_get_param: type coercion ────────────────────────────


class TestSafeGetParamCoercion:
    def test_int_from_float(self):
        assert safe_get_param({"x": 5.0}, "x", int) == 5

    def test_int_from_string(self):
        assert safe_get_param({"x": "5"}, "x", int) == 5

    def test_float_from_int(self):
        assert safe_get_param({"x": 5}, "x", float) == 5.0

    def test_float_from_string(self):
        assert safe_get_param({"x": "5.5"}, "x", float) == 5.5

    def test_str_from_int(self):
        assert safe_get_param({"x": 42}, "x", str) == "42"

    def test_str_from_float(self):
        assert safe_get_param({"x": 1.5}, "x", str) == "1.5"

    def test_str_from_bool(self):
        assert safe_get_param({"x": True}, "x", str) == "True"

    def test_list_from_tuple(self):
        assert safe_get_param({"x": (1, 2, 3)}, "x", list) == [1, 2, 3]

    def test_int_coerce_failure_raises(self):
        with pytest.raises(TypeError) as exc:
            safe_get_param({"x": "abc"}, "x", int)
        assert "int" in str(exc.value)
        assert "str" in str(exc.value)

    def test_list_coerce_failure_raises(self):
        with pytest.raises(TypeError):
            safe_get_param({"x": 42}, "x", list)


# ── safe_get_param: list unwrapping ───────────────────────────


class TestSafeGetParamListUnwrap:
    def test_unwrap_item(self):
        assert safe_get_param({"x": {"item": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_data(self):
        assert safe_get_param({"x": {"data": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_records(self):
        assert safe_get_param({"x": {"records": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_bars(self):
        assert safe_get_param({"x": {"bars": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_rows(self):
        assert safe_get_param({"x": {"rows": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_ohlcv(self):
        assert safe_get_param({"x": {"ohlcv": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_values(self):
        assert safe_get_param({"x": {"values": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_result(self):
        assert safe_get_param({"x": {"result": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_results(self):
        assert safe_get_param({"x": {"results": [1, 2]}}, "x", list) == [1, 2]

    def test_unwrap_dict_no_list_raises(self):
        """Dict with no list inside cannot be coerced to list."""
        with pytest.raises(TypeError):
            safe_get_param({"x": {"foo": 1}}, "x", list)

    def test_allow_unwrap_false(self):
        """allow_unwrap=False disables auto-unwrap."""
        with pytest.raises(TypeError):
            safe_get_param(
                {"x": {"item": [1, 2]}}, "x", list, allow_unwrap=False,
            )


# ── safe_get_param: dict unwrapping ───────────────────────────


class TestSafeGetParamDictUnwrap:
    def test_unwrap_item(self):
        d = {"k": "v"}
        assert safe_get_param({"x": {"item": d}}, "x", dict) == d

    def test_unwrap_data(self):
        d = {"k": "v"}
        assert safe_get_param({"x": {"data": d}}, "x", dict) == d

    def test_unwrap_args(self):
        d = {"k": "v"}
        assert safe_get_param({"x": {"args": d}}, "x", dict) == d


# ── safe_get_param: JSON stringification ──────────────────────


class TestSafeGetParamJSON:
    def test_parse_json_list(self):
        assert safe_get_param({"x": "[1, 2, 3]"}, "x", list) == [1, 2, 3]

    def test_parse_json_dict(self):
        assert safe_get_param({"x": '{"k": "v"}'}, "x", dict) == {"k": "v"}

    def test_invalid_json_passes_through_to_type_check(self):
        """Invalid JSON string for a list param → cannot parse → type error."""
        with pytest.raises(TypeError):
            safe_get_param({"x": "not json"}, "x", list)


# ── try_unwrap_list / try_unwrap_dict ────────────────────────


class TestTryUnwrap:
    def test_unwrap_list_finds_item(self):
        assert try_unwrap_list({"item": [1, 2]}) == [1, 2]

    def test_unwrap_list_no_match_returns_none(self):
        assert try_unwrap_list({"foo": 1}) is None

    def test_unwrap_list_not_dict_returns_none(self):
        assert try_unwrap_list([1, 2]) is None
        assert try_unwrap_list("string") is None
        assert try_unwrap_list(None) is None

    def test_unwrap_dict_finds_item(self):
        assert try_unwrap_dict({"item": {"k": "v"}}) == {"k": "v"}

    def test_unwrap_dict_no_match_returns_none(self):
        assert try_unwrap_dict({"foo": 1}) is None

    def test_unwrap_dict_not_dict_returns_none(self):
        assert try_unwrap_dict([1, 2]) is None
        assert try_unwrap_dict("string") is None


# ── err_actionable ────────────────────────────────────────────


class TestErrActionable:
    def test_minimal(self):
        out = json.loads(err_actionable("oops"))
        assert out == {"status": "error", "error": "oops"}

    def test_with_received(self):
        out = json.loads(err_actionable("bad", received=[1, 2, 3]))
        assert out["received"] == [1, 2, 3]
        assert "status" not in out["received"]  # plain value, not a payload

    def test_with_expected(self):
        out = json.loads(err_actionable("bad", expected="list[str]"))
        assert out["expected"] == "list[str]"

    def test_with_fix(self):
        out = json.loads(err_actionable("bad", fix="call get_market_data first"))
        assert out["fix"] == "call get_market_data first"

    def test_with_tool(self):
        out = json.loads(err_actionable("bad", tool="import_data"))
        assert out["tool"] == "import_data"

    def test_with_extra(self):
        out = json.loads(err_actionable("bad", extra={"code": "600519.SH"}))
        assert out["code"] == "600519.SH"

    def test_full(self):
        out = json.loads(err_actionable(
            "bad shape",
            received={"item": [1, 2]},
            expected="list[record]",
            fix="call get_market_data then call import_data(data=<result>)",
            tool="import_data",
            extra={"code": "600519.SH"},
        ))
        assert out["status"] == "error"
        assert out["error"] == "bad shape"
        assert out["received"] == {"item": [1, 2]}
        assert out["expected"] == "list[record]"
        assert "get_market_data" in out["fix"]
        assert out["tool"] == "import_data"
        assert out["code"] == "600519.SH"


# ── truncate ─────────────────────────────────────────────────


class TestTruncate:
    def test_short_string_passthrough(self):
        assert truncate("hello") == "hello"

    def test_long_string_truncated(self):
        long = "x" * 300
        out = truncate(long, max_len=50)
        assert out.startswith("x" * 50)
        assert "truncated" in out
        assert "300" in out

    def test_dict_truncated_recursively(self):
        d = {"short": "a", "long": "x" * 300}
        out = truncate(d, max_len=50)
        assert out["short"] == "a"
        assert out["long"].startswith("x" * 50)

    def test_list_truncated(self):
        long_list = list(range(20))
        out = truncate(long_list, max_len=50)
        assert len(out) == 6  # 5 items + 1 "... (total 20 items)"

    def test_int_passthrough(self):
        assert truncate(42) == 42

    def test_none_passthrough(self):
        assert truncate(None) is None
