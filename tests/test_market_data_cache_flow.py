"""Tests for the get_market_data → parquet cache → commit_market_data flow.

Covers the context-overflow fix (docs/context-overflow-fix.md):
- get_market_data returns a compact summary + cache_keys (NOT full OHLCV).
- commit_market_data merges cached parquet into the workspace DuckDB.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("STRATEGY_RESEARCH_CACHE_DIR", "/tmp/sr_test_cache")


@pytest.fixture(autouse=True)
def _clean_cache():
    shutil.rmtree("/tmp/sr_test_cache", ignore_errors=True)
    yield
    shutil.rmtree("/tmp/sr_test_cache", ignore_errors=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


class _FakeLoader:
    name = "fake"

    def is_available(self):
        return True

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None):
        out = {}
        for c in codes:
            dates = pd.date_range(start_date, periods=10, freq="D")
            out[c] = pd.DataFrame({
                "date": dates,
                "open": 10.0, "high": 11.0, "low": 9.5,
                "close": 10.5, "volume": 1000,
            })
        return out


@pytest.fixture
def tools():
    # Stub the loader registry + market detection so the tool finds the
    # fake loader without network.
    import strategy_research.core.data_source.registry as dsr
    from strategy_research.core.agent.builtin_tools.data_tools import (
        CommitMarketDataTool,
        GetMarketDataTool,
    )
    dsr.LOADER_REGISTRY["fake"] = _FakeLoader
    dsr.detect_market = lambda code: "a_share"
    return GetMarketDataTool(), CommitMarketDataTool()


def _run_get(tool, codes, start="2023-01-01", end="2023-01-10", source="fake"):
    raw = tool.execute(
        codes=codes, start_date=start, end_date=end, source=source,
    )
    return json.loads(raw)


class TestGetMarketDataSummary:
    def test_returns_summary_not_full_rows(self, tools):
        gmd, _ = tools
        result = _run_get(gmd, ["600519.SH", "000858.SZ"])
        assert result["status"] == "ok"
        # Full data must NOT be present (context-overflow fix)
        assert "data" not in result
        # cache_keys present per code
        assert set(result["cached"].keys()) == {"600519.SH", "000858.SZ"}
        for key in result["cached"].values():
            assert len(key) == 16
        # summary has stats, not rows
        s = result["summary"]["600519.SH"]
        assert s["rows"] == 10
        assert "first_close" in s and "last_close" in s
        assert "avg_volume" in s
        # preview limited to 5 rows
        assert len(result["preview"]["600519.SH"]) == 5
        # note points to commit_market_data
        assert "commit_market_data" in result["meta"]["note"]

    def test_empty_code_marks_summary_empty(self, tools):
        gmd, _ = tools
        raw = gmd.execute(
            codes=["600519.SH"], start_date="2023-01-01",
            end_date="2023-01-10", source="fake",
        )
        result = json.loads(raw)
        assert result["status"] == "ok"

    def test_parquet_files_written_to_cache(self, tools, monkeypatch):
        gmd, _ = tools
        from strategy_research.core.data_source.cache import _cache_root
        root = _cache_root()
        _run_get(gmd, ["600519.SH"])
        parquets = list(root.glob("*.parquet"))
        assert len(parquets) == 1


class TestCommitMarketData:
    def test_commit_merges_into_duckdb(self, tools, workspace):
        gmd, cmt = tools
        result = _run_get(gmd, ["600519.SH", "000858.SZ"])
        cache_keys = [result["cached"]["600519.SH"], result["cached"]["000858.SZ"]]
        codes = ["600519.SH", "000858.SZ"]

        out = json.loads(cmt.execute(
            workspace=str(workspace),
            cache_keys=cache_keys,
            codes=codes,
            strategy_name="blue_chip",
        ))
        assert out["status"] == "ok"
        assert out["total_rows"] == 20
        assert len(out["committed"]) == 2

        from strategy_research.core.db import get_connection, init_db
        init_db(workspace)
        conn = get_connection(workspace)
        rows = conn.execute(
            "SELECT asset_code, COUNT(*) FROM price_data GROUP BY 1"
        ).fetchall()
        assert dict(rows) == {"600519.SH": 10, "000858.SZ": 10}

    def test_commit_mismatched_keys_errors(self, tools, workspace):
        _, cmt = tools
        out = json.loads(cmt.execute(
            workspace=str(workspace),
            cache_keys=["a", "b", "c"],
            codes=["600519.SH"],
            strategy_name="x",
        ))
        assert out["status"] == "error"
        assert "re-run get_market_data" in out["fix"]

    def test_commit_missing_key_reports_missing(self, tools, workspace):
        _, cmt = tools
        out = json.loads(cmt.execute(
            workspace=str(workspace),
            cache_keys=["deadbeef00000000"],
            codes=["600519.SH"],
            strategy_name="x",
        ))
        assert out["status"] == "ok"
        assert out["missing"] == ["600519.SH"]
        assert out["committed"] == []


class TestCommitRegistered:
    def test_commit_tool_in_default_registry(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        reg = build_default_registry()
        assert reg.get("commit_market_data") is not None
        assert reg.get("get_market_data") is not None


class TestCacheKeyDeterminism:
    def test_cache_key_stable(self, tools):
        from strategy_research.core.data_source.cache import make_cache_key
        k1 = make_cache_key("fake", "600519.SH", "1D", "2023-01-01", "2023-01-10")
        k2 = make_cache_key("fake", "600519.SH", "1D", "2023-01-01", "2023-01-10")
        assert k1 == k2 == "0ca8e47f3cf1213e"

    def test_cache_key_changes_with_params(self, tools):
        from strategy_research.core.data_source.cache import make_cache_key
        a = make_cache_key("fake", "600519.SH", "1D", "2023-01-01", "2023-01-10")
        b = make_cache_key("fake", "600519.SH", "1D", "2023-02-01", "2023-01-10")
        assert a != b

    def test_cache_key_matches_get_output(self, tools):
        """get_market_data returns the same key the loader cache uses."""
        from strategy_research.core.data_source.cache import make_cache_key
        gmd, _ = tools
        result = _run_get(gmd, ["600519.SH"])
        key = result["cached"]["600519.SH"]
        expected = make_cache_key("fake", "600519.SH", "1D", "2023-01-01", "2023-01-10")
        assert key == expected


class TestGetMarketDataEdgeCases:
    def test_summary_fields_complete(self, tools):
        gmd, _ = tools
        result = _run_get(gmd, ["600519.SH"])
        s = result["summary"]["600519.SH"]
        for field in ("rows", "status", "cache_key", "first_close", "last_close",
                      "close_min", "close_max", "avg_volume"):
            assert field in s, f"summary missing {field}"

    def test_preview_bounded_to_5_rows(self, tools):
        gmd, _ = tools
        result = _run_get(gmd, ["600519.SH"])
        assert len(result["preview"]["600519.SH"]) <= 5

    def test_unavailable_source_errors(self, tools):
        """Explicit source that exists but is unavailable → actionable error."""
        import strategy_research.core.data_source.registry as dsr

        class _Unavailable:
            name = "down"
            def is_available(self):
                return False
        dsr.LOADER_REGISTRY["down"] = _Unavailable
        gmd, _ = tools
        raw = gmd.execute(
            codes=["600519.SH"], start_date="2023-01-01",
            end_date="2023-01-10", source="down",
        )
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "not available" in result["error"]


class TestCommitMarketDataEdgeCases:
    def test_repeated_commit_is_idempotent(self, tools, workspace):
        """INSERT OR REPLACE → same rows, no duplication."""
        gmd, cmt = tools
        result = _run_get(gmd, ["600519.SH"])
        key = result["cached"]["600519.SH"]

        def _commit():
            return json.loads(cmt.execute(
                workspace=str(workspace), cache_keys=[key], codes=["600519.SH"],
                strategy_name="blue_chip",
            ))

        first = _commit()
        second = _commit()
        assert first["total_rows"] == 10
        assert second["total_rows"] == 10  # replace, not append

        from strategy_research.core.db import get_connection, init_db
        init_db(workspace)
        conn = get_connection(workspace)
        n = conn.execute(
            "SELECT COUNT(*) FROM price_data WHERE asset_code='600519.SH'"
        ).fetchone()[0]
        assert n == 10

    def test_missing_workspace_errors(self, tools):
        _, cmt = tools
        out = json.loads(cmt.execute(cache_keys=["k"], codes=["c"]))
        assert out["status"] == "error"
        assert "workspace" in out["fix"]

    def test_empty_codes_errors(self, tools, workspace):
        _, cmt = tools
        out = json.loads(cmt.execute(
            workspace=str(workspace), cache_keys=[], codes=[], strategy_name="x",
        ))
        assert out["status"] == "error"
