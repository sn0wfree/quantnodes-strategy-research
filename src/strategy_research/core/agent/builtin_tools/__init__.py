"""6 BaseTool tools for agent code interaction.

All tools accept `workspace` (Path-like) via kwargs injection by AgentLoop.
Each tool returns a JSON string (success or error envelope).

Tools:
    ReadFileTool      - read files inside workspace
    WriteFileTool     - write files (sandbox + AST guard for .py)
    RunBacktestTool   - invoke core.backtest.run_backtest_from_yaml
    ComputeFactorTool - invoke core.compute_factor.compute_factor
    GitDiffTool       - subprocess wrapper for git diff
    ListHistoryTool   - list runs from results.tsv + runs/ directory
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from ..sandbox import (
    ASTValidationError,
    PathValidationError,
    PathWhitelist,
    validate_python_source,
)
from ..tools import BaseTool, ToolRegistry
from ...compute_factor import compute_factor
from ...backtest import run_backtest_from_yaml

logger = logging.getLogger(__name__)


# ── Shared helpers ───────────────────────────────────────────────────


def _workspace_from_kwargs(kwargs: dict[str, Any]) -> Path:
    """Extract and normalize workspace path from kwargs."""
    ws = kwargs.get("workspace")
    if ws is None:
        raise ValueError("missing required kwarg 'workspace'")
    if isinstance(ws, str):
        ws = Path(ws)
    if not isinstance(ws, Path):
        raise ValueError(f"workspace must be Path or str, got {type(ws).__name__}")
    return ws.resolve()


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps(
        {"status": "error", "error": str(message), **extra},
        ensure_ascii=False,
    )


# ── 1. ReadFileTool ─────────────────────────────────────────────────


class ReadFileTool(BaseTool):
    """Read a file from the workspace (read-only)."""

    name = "read_file"
    description = (
        "Read a file from the workspace. Returns file contents (with optional "
        "line limit). Path is relative to workspace and must be under an allowed "
        "read root (strategies/templates/memory/logs/data/docs/.)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "path": {"type": "string", "description": "File path relative to workspace."},
            "limit": {"type": "integer", "description": "Max number of lines to return."},
            "offset": {"type": "integer", "description": "Line offset to start reading (0-indexed)."},
        },
        "required": ["workspace", "path"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        path = kwargs.get("path")
        if not isinstance(path, str) or not path:
            return _err("missing or invalid 'path'")
        limit = kwargs.get("limit")
        offset = kwargs.get("offset", 0) or 0

        wl = PathWhitelist(workspace=workspace)
        try:
            resolved = wl.resolve_read(path)
        except PathValidationError as exc:
            return _err(str(exc))

        if not resolved.exists():
            return _err(f"file not found: {path}", path=str(resolved))
        if not resolved.is_file():
            return _err(f"not a regular file: {path}", path=str(resolved))

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _err(f"file is not valid UTF-8: {path}", path=str(resolved))
        except OSError as exc:
            return _err(f"read failed: {exc}")

        all_lines = content.splitlines()
        if offset:
            all_lines = all_lines[offset:]
        if limit is not None:
            all_lines = all_lines[: int(limit)]
        output = "\n".join(all_lines)

        return _ok({
            "path": str(resolved),
            "content": output,
            "total_lines": len(content.splitlines()),
            "returned_lines": len(all_lines),
        })


# ── 2. WriteFileTool ────────────────────────────────────────────────


class WriteFileTool(BaseTool):
    """Write content to a file in the workspace (sandbox + AST guard)."""

    name = "write_file"
    description = (
        "Write content to a file in the workspace. Path must be under an allowed "
        "write root (strategies/templates/memory/logs). .py files are AST-validated; "
        "dangerous code (exec/eval, blocked imports, dunder access) is rejected."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "path": {"type": "string", "description": "File path relative to workspace."},
            "content": {"type": "string", "description": "File content to write."},
        },
        "required": ["workspace", "path", "content"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        path = kwargs.get("path")
        content = kwargs.get("content")
        if not isinstance(path, str) or not path:
            return _err("missing or invalid 'path'")
        if not isinstance(content, str):
            return _err("missing or invalid 'content'")

        # AST guard for .py files
        if path.endswith(".py"):
            ok, msg = validate_python_source(content)
            if not ok:
                return _err(f"AST validation failed: {msg}")

        wl = PathWhitelist(workspace=workspace)
        try:
            resolved = wl.resolve_write(path)
        except PathValidationError as exc:
            return _err(str(exc))

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _err(f"write failed: {exc}")

        return _ok({
            "path": str(resolved),
            "bytes_written": len(content.encode("utf-8")),
        })


# ── 3. RunBacktestTool ──────────────────────────────────────────────


class RunBacktestTool(BaseTool):
    """Run a backtest using the workspace's strategy configuration."""

    name = "run_backtest"
    description = (
        "Run a backtest for the given strategy. Reads config.yaml from "
        "strategies/<strategy_name>/ and produces a new run under runs/."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "strategy_name": {"type": "string", "description": "Strategy name."},
            "action": {"type": "string", "description": "Action label (e.g. 'manual', 'agent')."},
            "description": {"type": "string", "description": "Optional description."},
            "yaml_path": {"type": "string", "description": "Override YAML config path."},
        },
        "required": ["workspace", "strategy_name"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        strategy_name = kwargs.get("strategy_name")
        if not isinstance(strategy_name, str) or not strategy_name:
            return _err("missing or invalid 'strategy_name'")
        action = kwargs.get("action") or "agent"
        description = kwargs.get("description") or ""
        yaml_path = kwargs.get("yaml_path")
        if yaml_path is not None:
            yaml_path = str(workspace / yaml_path)

        try:
            result = run_backtest_from_yaml(
                workspace_path=workspace,
                strategy_name=strategy_name,
                yaml_path=yaml_path,
                action=action,
                description=description,
            )
        except Exception as exc:                    # noqa: BLE001
            logger.exception("run_backtest failed")
            return _err(f"backtest raised: {exc}")

        if not result.get("success", False):
            return _err(
                result.get("error", "unknown backtest failure"),
                run=result.get("run", ""),
                metrics=result.get("metrics", {}),
            )

        return _ok({
            "run": result.get("run", ""),
            "strategy": strategy_name,
            "metrics": result.get("metrics", {}),
            "status": result.get("status", "pending"),
        })


# ── 4. ComputeFactorTool ────────────────────────────────────────────


class ComputeFactorTool(BaseTool):
    """Compute a factor expression on workspace price data.

    The compute_factor DSL expects a single-asset wide-format DataFrame with
    columns like 'close', 'open', 'high', 'low', 'volume'. The agent should
    specify an `asset` to compute on. Defaults to the first available asset.
    """

    name = "compute_factor"
    description = (
        "Compute a factor expression (e.g. 'ts_mean(close, 20) / ts_mean(close, 60) - 1') "
        "on a single asset's price data from the workspace's DuckDB. Returns a "
        "sample of the resulting series."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "factor_code": {"type": "string", "description": "Factor expression."},
            "asset": {"type": "string", "description": "Asset code to compute on (default: first asset)."},
            "factor_name": {"type": "string", "description": "Optional factor name."},
            "n_samples": {"type": "integer", "description": "How many sample values to return (default 5)."},
        },
        "required": ["workspace", "factor_code"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        factor_code = kwargs.get("factor_code")
        if not isinstance(factor_code, str) or not factor_code:
            return _err("missing or invalid 'factor_code'")
        asset = kwargs.get("asset")
        factor_name = kwargs.get("factor_name") or ""
        n_samples = int(kwargs.get("n_samples", 5))

        # Load price data from workspace DuckDB
        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:                    # noqa: BLE001
            return _err(f"db open failed: {exc}")
        if conn is None:
            return _err("workspace has no DuckDB; run cmd_import first")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume "
                "FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:                    # noqa: BLE001
            return _err(f"ohlcv query failed: {exc} (table may not exist)")

        if prices_df.empty:
            return _err("ohlcv table is empty")

        # Pick asset (default: first)
        available_assets = sorted(prices_df["asset"].unique())
        if not available_assets:
            return _err("no assets in ohlcv table")
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return _err(
                f"asset '{asset}' not found; available: {available_assets[:5]}..."
                if len(available_assets) > 5 else
                f"asset '{asset}' not found; available: {available_assets}"
            )

        # Build single-asset wide DataFrame (date index, ohlcv columns)
        asset_df = prices_df[prices_df["asset"] == asset].copy()
        asset_df = asset_df.set_index("date")[["open", "high", "low", "close", "volume"]]
        asset_df = asset_df.sort_index()

        try:
            series = compute_factor(factor_code, asset_df, factor_name=factor_name)
        except Exception as exc:                    # noqa: BLE001
            logger.exception("compute_factor failed")
            return _err(f"compute failed: {exc}")

        # Sample the result
        non_null = series.dropna()
        if len(non_null) == 0:
            return _err(
                "factor produced no non-null values",
                factor_name=factor_name, asset=asset,
            )
        sample = non_null.head(n_samples).to_dict()
        sample = {str(k): (None if v != v else float(v)) for k, v in sample.items()}

        return _ok({
            "factor_name": factor_name or "(unnamed)",
            "factor_code": factor_code,
            "asset": asset,
            "n_total": int(len(series)),
            "n_non_null": int(len(non_null)),
            "sample": sample,
            "first_date": str(series.index.min()) if len(series) else None,
            "last_date": str(series.index.max()) if len(series) else None,
        })


# ── 5. GitDiffTool ──────────────────────────────────────────────────


class GitDiffTool(BaseTool):
    """Show git diff of the workspace."""

    name = "git_diff"
    description = (
        "Show git diff for the workspace. By default shows unstaged changes; "
        "set staged=true for staged-only, or pass ref1/ref2 to compare specific commits."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "staged": {"type": "boolean", "description": "Show staged changes only."},
            "ref1": {"type": "string", "description": "First ref for comparison."},
            "ref2": {"type": "string", "description": "Second ref for comparison."},
            "pathspec": {"type": "string", "description": "Limit diff to this path."},
            "max_lines": {"type": "integer", "description": "Max diff lines to return (default 200)."},
        },
        "required": ["workspace"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        staged = bool(kwargs.get("staged", False))
        ref1 = kwargs.get("ref1")
        ref2 = kwargs.get("ref2")
        pathspec = kwargs.get("pathspec")
        max_lines = int(kwargs.get("max_lines", 200))

        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        if ref1:
            cmd.append(ref1)
            if ref2:
                cmd.append(ref2)
        if pathspec:
            # Sanitize pathspec (basic guard against flag injection)
            if pathspec.startswith("-"):
                return _err(f"pathspec must not start with '-': {pathspec}")
            cmd.extend(["--", pathspec])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _err("git diff timed out (30s)")
        except FileNotFoundError:
            return _err("git not found in PATH")
        except Exception as exc:                    # noqa: BLE001
            return _err(f"git diff failed: {exc}")

        if result.returncode != 0:
            return _err(f"git diff returned {result.returncode}: {result.stderr.strip()}")

        diff = result.stdout
        lines = diff.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            diff = "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"

        return _ok({
            "diff": diff,
            "total_lines": len(lines),
            "truncated": truncated,
            "staged": staged,
        })


# ── 6. ListHistoryTool ──────────────────────────────────────────────


class ListHistoryTool(BaseTool):
    """List past runs from results.tsv and runs/ directory."""

    name = "list_history"
    description = (
        "List past backtest runs. Reads results.tsv and runs/ directory. "
        "Optionally filter by strategy_name. Returns summary rows with key metrics."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "strategy_name": {"type": "string", "description": "Filter by strategy name."},
            "limit": {"type": "integer", "description": "Max rows to return (default 20)."},
        },
        "required": ["workspace"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        strategy_name = kwargs.get("strategy_name")
        limit = int(kwargs.get("limit", 20))

        results_path: Path | None = None
        if strategy_name:
            cand = workspace / "strategies" / strategy_name / "runs" / "results.tsv"
            if cand.exists():
                results_path = cand
        else:
            # Search all strategies for results.tsv
            strategies_dir = workspace / "strategies"
            if strategies_dir.exists():
                for d in sorted(strategies_dir.iterdir()):
                    cand = d / "runs" / "results.tsv"
                    if cand.exists():
                        results_path = cand
                        break

        if results_path is None or not results_path.exists():
            return _ok({
                "runs": [],
                "source": None,
                "message": "no results.tsv found",
            })

        try:
            with open(results_path, encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError as exc:
            return _err(f"read failed: {exc}")

        if not lines:
            return _ok({"runs": [], "source": str(results_path)})

        header = lines[0].split("\t")
        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < len(header):
                continue
            row = {h: parts[i] for i, h in enumerate(header)}
            rows.append(row)

        # Sort by run name desc (newest first) and apply limit
        rows.sort(key=lambda r: r.get("run", ""), reverse=True)
        rows = rows[:limit]

        return _ok({
            "source": str(results_path),
            "n_rows": len(rows),
            "runs": rows,
        })


# ── Registry ─────────────────────────────────────────────────────────


def build_default_registry() -> ToolRegistry:
    """Build a ToolRegistry with all 6 tools.

    Tools are stateless; AgentLoop injects `workspace` per call.
    No workspace is bound at construction time.
    """
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(WriteFileTool())
    r.register(RunBacktestTool())
    r.register(ComputeFactorTool())
    r.register(GitDiffTool())
    r.register(ListHistoryTool())
    return r


__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "RunBacktestTool",
    "ComputeFactorTool",
    "GitDiffTool",
    "ListHistoryTool",
    "build_default_registry",
]