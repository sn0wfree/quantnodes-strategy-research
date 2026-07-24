"""Tests for the 11 BaseTool tools + registry."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import (
    ComputeFactorTool,
    GitDiffTool,
    ListHistoryTool,
    ListSkillsTool,
    LoadSkillTool,
    ReadFileTool,
    RunBacktestTool,
    WriteFileTool,
    build_default_registry,
)
from strategy_research.core.agent.tools import ToolRegistry


# ── Shared fixture ───────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Build a minimal workspace for tool tests."""
    (tmp_path / "strategies").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# test workspace\n")
    (tmp_path / "config.yaml").write_text("a: 1\n")
    return tmp_path


def parse_result(result: str) -> dict:
    return json.loads(result)


# ── Registry ─────────────────────────────────────────────────────────


class TestRegistry:
    def test_build_default_has_all_eleven(self):
        r = build_default_registry()
        names = sorted(r.tool_names)
        # 11 original tools + 3 web tools (web_search, read_url, read_document)
        expected_core = [
            "compute_factor", "factor_analysis", "git_diff", "list_history",
            "list_skills", "load_skill", "options_pricing", "pattern_recognition",
            "read_file", "run_backtest", "write_file",
        ]
        # All core tools must be present
        for name in expected_core:
            assert name in names, f"missing core tool: {name}"
        # Web tools may be present if dependencies are installed
        web_tools = ["web_search", "read_url", "read_document"]
        for name in web_tools:
            if name in names:
                expected_core.append(name)
        assert len(r) >= 11

    def test_registry_contains(self):
        r = build_default_registry()
        assert "read_file" in r
        assert "missing_tool" not in r

    def test_registry_execute(self):
        r = build_default_registry()
        out = r.execute("read_file", {"path": "README.md", "workspace": Path(".")})
        # Should be valid JSON
        d = json.loads(out)
        assert "status" in d


# ── ReadFileTool ─────────────────────────────────────────────────────


class TestReadFileTool:
    def test_read_existing_file(self, workspace: Path):
        (workspace / "README.md").write_text("hello\nworld\n")
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="README.md",
        ))
        assert result["status"] == "ok"
        assert result["content"] == "hello\nworld"
        assert result["returned_lines"] == 2

    def test_read_with_limit(self, workspace: Path):
        (workspace / "data" / "x.txt").write_text("\n".join(f"line{i}" for i in range(10)))
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="data/x.txt", limit=3,
        ))
        assert result["status"] == "ok"
        assert result["content"] == "line0\nline1\nline2"

    def test_read_with_offset(self, workspace: Path):
        (workspace / "data" / "x.txt").write_text("\n".join(f"line{i}" for i in range(10)))
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="data/x.txt", offset=7, limit=2,
        ))
        assert result["status"] == "ok"
        assert result["content"] == "line7\nline8"

    def test_read_nonexistent(self, workspace: Path):
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="data/nonexistent.txt",
        ))
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_read_outside_whitelist(self, workspace: Path):
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="../../../etc/passwd",
        ))
        assert result["status"] == "error"

    def test_read_absolute_path_blocked(self, workspace: Path):
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="/etc/passwd",
        ))
        assert result["status"] == "error"

    def test_read_missing_workspace(self):
        tool = ReadFileTool()
        result = parse_result(tool.execute(path="x"))
        assert result["status"] == "error"
        assert "workspace" in result["error"]

    def test_read_missing_path(self, workspace: Path):
        tool = ReadFileTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "error"

    def test_read_directory_as_file(self, workspace: Path):
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="strategies",
        ))
        assert result["status"] == "error"
        assert "not a regular file" in result["error"]

    def test_read_binary_file(self, workspace: Path):
        # Use bytes that are NOT valid UTF-8 (FF FE pattern)
        (workspace / "data" / "x.bin").write_bytes(b"\xff\xfe\xfd\xfc")
        tool = ReadFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="data/x.bin",
        ))
        assert result["status"] == "error"
        assert "UTF-8" in result["error"]


# ── WriteFileTool ────────────────────────────────────────────────────


class TestWriteFileTool:
    def test_write_new_file(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="strategies/foo/strategy.py",
            content="# new strategy\nx = 1\n",
        ))
        assert result["status"] == "ok"
        assert result["bytes_written"] > 0
        f = workspace / "strategies" / "foo" / "strategy.py"
        assert f.exists()
        assert f.read_text() == "# new strategy\nx = 1\n"

    def test_write_overwrites(self, workspace: Path):
        (workspace / "templates" / "strategy.py").write_text("old")
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="templates/strategy.py", content="new",
        ))
        assert result["status"] == "ok"
        assert (workspace / "templates" / "strategy.py").read_text() == "new"

    def test_write_creates_parent_dirs(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="strategies/a/b/c/d/file.py",
            content="x = 1",
        ))
        assert result["status"] == "ok"
        assert (workspace / "strategies" / "a" / "b" / "c" / "d" / "file.py").exists()

    def test_write_blocks_exec(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="strategies/bad.py",
            content="exec('print(1)')",
        ))
        assert result["status"] == "error"
        assert "AST" in result["error"]

    def test_write_blocks_import_os(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="strategies/bad.py",
            content="import os\n",
        ))
        assert result["status"] == "error"
        assert "AST" in result["error"]

    def test_write_blocks_outside_whitelist(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="data/foo.txt",  # data/ is read-only
            content="x",
        ))
        assert result["status"] == "error"

    def test_write_blocks_absolute_path(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="/tmp/evil.txt",
            content="x",
        ))
        assert result["status"] == "error"

    def test_write_blocks_dotdot(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="../escape.py",
            content="x",
        ))
        assert result["status"] == "error"

    def test_write_non_python_no_ast_check(self, workspace: Path):
        # Non-.py files skip AST validation
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="memory/notes.md",
            content="any content at all\n",
        ))
        assert result["status"] == "ok"

    def test_write_pandas_passes(self, workspace: Path):
        # Realistic strategy code with pandas/numpy should pass
        code = """
import pandas as pd
import numpy as np

PARAMS = {'top_n': 10}

FACTOR_EXPRS = [
    {'factor_name': 'mom', 'factor_code': 'ts_mean(close, 20) / ts_mean(close, 60) - 1'},
]

class MyStrategy:
    def __init__(self):
        self.params = PARAMS
    def run(self):
        return np.array([1, 2, 3])
"""
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace,
            path="strategies/momentum/strategy.py",
            content=code,
        ))
        assert result["status"] == "ok", result.get("error")

    def test_write_missing_content(self, workspace: Path):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=workspace, path="memory/x.txt",
        ))
        assert result["status"] == "error"

    def test_write_invalid_workspace_type(self):
        tool = WriteFileTool()
        result = parse_result(tool.execute(
            workspace=123,  # type: ignore
            path="memory/x.txt", content="x",
        ))
        assert result["status"] == "error"


# ── RunBacktestTool ──────────────────────────────────────────────────


class TestRunBacktestTool:
    def test_missing_strategy_dir(self, workspace: Path):
        tool = RunBacktestTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="nonexistent",
        ))
        assert result["status"] == "error"
        assert "配置文件不存在" in result["error"] or "not found" in result["error"].lower()

    def test_missing_strategy_name(self, workspace: Path):
        tool = RunBacktestTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "error"

    def test_existing_strategy(self, workspace: Path):
        # Create a minimal strategy
        sdir = workspace / "strategies" / "foo"
        sdir.mkdir()
        (sdir / "config.yaml").write_text("""strategy:
  name: foo
  type: rotation
data:
  source: sample
rebalance:
  freq: 20
""")
        tool = RunBacktestTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="foo", action="agent_test",
        ))
        # Could be ok (if sample data works) or error (if data missing)
        # The point is it shouldn't crash with a Python exception
        assert "status" in result

    def test_run_backtest_default_action(self, workspace: Path):
        sdir = workspace / "strategies" / "foo"
        sdir.mkdir()
        (sdir / "config.yaml").write_text("strategy:\n  name: foo\n  type: rotation\n")
        tool = RunBacktestTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="foo",
        ))
        # Should default action to 'agent' (no crash)
        assert "status" in result

    def test_run_backtest_missing_workspace(self):
        tool = RunBacktestTool()
        result = parse_result(tool.execute(strategy_name="x"))
        assert result["status"] == "error"


# ── ComputeFactorTool ────────────────────────────────────────────────


class TestComputeFactorTool:
    def _populate_ohlcv(self, workspace: Path):
        """Insert sample OHLCV data into workspace DuckDB."""
        try:
            from strategy_research.core.db import get_connection
        except ImportError:
            pytest.skip("db module not available")
        conn = get_connection(workspace)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                date DATE,
                asset VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE
            )
        """)
        # Insert 30 days × 2 assets
        import pandas as pd
        rows = []
        for i in range(30):
            for asset in ("A", "B"):
                rows.append({
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                    "asset": asset,
                    "open": 100.0 + i,
                    "high": 101.0 + i,
                    "low": 99.0 + i,
                    "close": 100.0 + i,
                    "volume": 1000.0,
                })
        df = pd.DataFrame(rows)
        conn.register("df_temp", df)
        conn.execute("INSERT INTO ohlcv SELECT * FROM df_temp")
        conn.unregister("df_temp")

    def test_no_workspace_db(self, workspace: Path):
        tool = ComputeFactorTool()
        result = parse_result(tool.execute(
            workspace=workspace, factor_code="ts_return(close, 1)",
        ))
        assert result["status"] == "error"

    def test_empty_ohlcv(self, workspace: Path):
        try:
            from strategy_research.core.db import get_connection
            conn = get_connection(workspace)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    date DATE, asset VARCHAR,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE
                )
            """)
        except ImportError:
            pytest.skip("db module not available")

        tool = ComputeFactorTool()
        result = parse_result(tool.execute(
            workspace=workspace, factor_code="ts_return(close, 1)",
        ))
        assert result["status"] == "error"
        assert "empty" in result["error"].lower()

    def test_simple_factor(self, workspace: Path):
        self._populate_ohlcv(workspace)
        tool = ComputeFactorTool()
        # Use the project's custom DSL (ts_mean, ts_return, etc.)
        result = parse_result(tool.execute(
            workspace=workspace,
            factor_code="ts_return(close, 1)",
            factor_name="ret1",
            n_samples=3,
        ))
        assert result["status"] == "ok"
        assert result["factor_name"] == "ret1"
        assert result["n_total"] > 0
        assert result["n_non_null"] > 0
        assert len(result["sample"]) <= 3

    def test_missing_factor_code(self, workspace: Path):
        tool = ComputeFactorTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "error"


# ── GitDiffTool ──────────────────────────────────────────────────────


class TestGitDiffTool:
    def _git_init(self, workspace: Path) -> None:
        subprocess.run(["git", "init"], cwd=str(workspace), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(workspace), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"],
                       cwd=str(workspace), capture_output=True, check=True)
        # Initial commit so subsequent diffs work
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                       cwd=str(workspace), capture_output=True, check=True)

    def test_unstaged_diff(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "new.txt").write_text("hello")
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "ok"
        assert "diff" in result
        # new.txt is untracked; may or may not show in diff depending on git version
        # At minimum, the call should succeed

    def test_staged_diff(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "new.txt").write_text("hello")
        subprocess.run(["git", "add", "new.txt"], cwd=str(workspace),
                       capture_output=True, check=True)
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace, staged=True))
        assert result["status"] == "ok"
        assert "+hello" in result["diff"]

    def test_empty_diff(self, workspace: Path):
        self._git_init(workspace)
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "ok"
        assert result["diff"] == ""

    def test_modified_tracked_file(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "a.txt").write_text("v1")
        subprocess.run(["git", "add", "a.txt"], cwd=str(workspace), capture_output=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=str(workspace), capture_output=True)
        (workspace / "a.txt").write_text("v2")
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "ok"
        assert "-v1" in result["diff"]
        assert "+v2" in result["diff"]

    def test_pathspec_filter(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "wanted.txt").write_text("v1")
        (workspace / "unwanted.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=str(workspace), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(workspace), capture_output=True)
        (workspace / "wanted.txt").write_text("v2")
        (workspace / "unwanted.txt").write_text("v2")
        tool = GitDiffTool()
        result = parse_result(tool.execute(
            workspace=workspace, pathspec="wanted.txt",
        ))
        assert result["status"] == "ok"
        assert "wanted" in result["diff"]
        assert "unwanted" not in result["diff"]

    def test_pathspec_flag_injection_blocked(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "wanted.txt").write_text("x")
        tool = GitDiffTool()
        result = parse_result(tool.execute(
            workspace=workspace, pathspec="--upload-pack=evil",
        ))
        assert result["status"] == "error"

    def test_truncation(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "big.txt").write_text("\n".join(f"line{i}" for i in range(500)))
        subprocess.run(["git", "add", "big.txt"], cwd=str(workspace), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(workspace), capture_output=True)
        (workspace / "big.txt").write_text("\n".join(f"line{i}-changed" for i in range(500)))
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace, max_lines=10))
        assert result["status"] == "ok"
        assert result["truncated"] is True
        assert result["total_lines"] > 10

    def test_no_git_repo(self, workspace: Path):
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace))
        # No repo → git diff returns error code 128 → tool reports error
        assert result["status"] == "error"

    def test_ref_comparison(self, workspace: Path):
        self._git_init(workspace)
        (workspace / "a.txt").write_text("v1")
        subprocess.run(["git", "add", "a.txt"], cwd=str(workspace), capture_output=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=str(workspace), capture_output=True)
        (workspace / "a.txt").write_text("v2")
        tool = GitDiffTool()
        result = parse_result(tool.execute(workspace=workspace, ref1="HEAD"))
        assert result["status"] == "ok"
        assert "-v1" in result["diff"]
        assert "+v2" in result["diff"]


# ── ListHistoryTool ──────────────────────────────────────────────────


class TestListHistoryTool:
    def test_no_results_tsv(self, workspace: Path):
        tool = ListHistoryTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "ok"
        assert result["runs"] == []

    def test_with_results_tsv(self, workspace: Path):
        (workspace / "strategies" / "foo" / "runs").mkdir(parents=True)
        tsv = workspace / "strategies" / "foo" / "runs" / "results.tsv"
        tsv.write_text(
            "run\tcalmar\tsharpe\tstatus\n"
            "run_0001\t0.5\t0.8\tkeep\n"
            "run_0002\t0.6\t0.9\tdiscard\n"
        )
        tool = ListHistoryTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="foo",
        ))
        assert result["status"] == "ok"
        assert result["n_rows"] == 2
        runs = result["runs"]
        assert runs[0]["run"] == "run_0002"  # newest first
        assert runs[1]["run"] == "run_0001"

    def test_with_limit(self, workspace: Path):
        (workspace / "strategies" / "foo" / "runs").mkdir(parents=True)
        tsv = workspace / "strategies" / "foo" / "runs" / "results.tsv"
        lines = ["run\tcalmar"]
        for i in range(20):
            lines.append(f"run_{i:04d}\t0.5")
        tsv.write_text("\n".join(lines) + "\n")
        tool = ListHistoryTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="foo", limit=5,
        ))
        assert result["status"] == "ok"
        assert result["n_rows"] == 5

    def test_search_all_strategies(self, workspace: Path):
        # Without strategy_name, should find first results.tsv under strategies/
        (workspace / "strategies" / "bar" / "runs").mkdir(parents=True)
        tsv = workspace / "strategies" / "bar" / "runs" / "results.tsv"
        tsv.write_text("run\tcalmar\nrun_0001\t0.4\n")
        tool = ListHistoryTool()
        result = parse_result(tool.execute(workspace=workspace))
        assert result["status"] == "ok"
        assert "bar" in result["source"]

    def test_empty_tsv(self, workspace: Path):
        (workspace / "strategies" / "foo" / "runs").mkdir(parents=True)
        tsv = workspace / "strategies" / "foo" / "runs" / "results.tsv"
        tsv.write_text("run\tcalmar\n")  # header only
        tool = ListHistoryTool()
        result = parse_result(tool.execute(
            workspace=workspace, strategy_name="foo",
        ))
        assert result["status"] == "ok"
        assert result["runs"] == []

    def test_missing_workspace(self):
        tool = ListHistoryTool()
        result = parse_result(tool.execute())
        assert result["status"] == "error"


# ── Integration: registry + tool execution ──────────────────────────


class TestIntegration:
    def test_registry_execute_read_file(self, workspace: Path):
        (workspace / "memory" / "notes.md").write_text("integration test\n")
        r = build_default_registry()
        result = json.loads(r.execute("read_file", {
            "workspace": workspace, "path": "memory/notes.md",
        }))
        assert result["status"] == "ok"
        assert "integration test" in result["content"]

    def test_registry_execute_unknown_tool(self):
        r = build_default_registry()
        # ToolRegistry.execute returns error JSON for unknown tool
        result = parse_result(r.execute("nonexistent_tool", {}))
        assert result["status"] == "error"

    def test_all_tools_have_required_metadata(self):
        r = build_default_registry()
        for name in r.tool_names:
            tool = r.get(name)
            assert tool is not None
            assert tool.name == name
            assert isinstance(tool.description, str) and tool.description
            assert tool.parameters.get("type") == "object"
            assert "properties" in tool.parameters

    def test_kwargs_injection_workspace(self, workspace: Path):
        """All tools should accept 'workspace' kwarg and respect it."""
        (workspace / "README.md").write_text("ws-specific content")
        r = build_default_registry()

        # ReadFile
        result = json.loads(r.execute("read_file", {
            "workspace": workspace, "path": "README.md",
        }))
        assert "ws-specific content" in result["content"]