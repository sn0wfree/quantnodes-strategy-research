"""11 BaseTool tools for agent code interaction.

All tools accept `workspace` (Path-like) via kwargs injection by AgentLoop.
Each tool returns a JSON string (success or error envelope).

Tools:
    ReadFileTool       - read files inside workspace
    WriteFileTool      - write files (sandbox + AST guard for .py)
    RunBacktestTool    - invoke core.backtest.run_backtest_from_yaml
    ComputeFactorTool  - invoke core.compute_factor.compute_factor
    GitDiffTool        - subprocess wrapper for git diff
    ListHistoryTool    - list runs from results.tsv + runs/ directory
    FactorAnalysisTool - factor IC/IR analysis
    PatternRecognitionTool - detect chart patterns
    ListSkillsTool     - list available methodology skills
    LoadSkillTool      - load full skill content by name
    OptionsPricingTool - Black-Scholes options pricing
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ...backtest import run_backtest_from_yaml
from ...compute_factor import compute_factor
from ..sandbox import (
    PathValidationError,
    PathWhitelist,
    validate_python_source,
)
from ..tools import BaseTool, ToolRegistry

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


# ── 7. FactorAnalysisTool ──────────────────────────────────────────


class FactorAnalysisTool(BaseTool):
    """Analyze factor IC/IR statistics."""

    name = "factor_analysis"
    description = (
        "Run factor IC/IR analysis on a factor expression. Computes IC mean, "
        "IC std, IR, IC>0 ratio, and returns statistical summary."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "factor_code": {"type": "string", "description": "Factor expression."},
            "asset": {"type": "string", "description": "Asset code (default: first)."},
            "forward_days": {"type": "integer", "description": "Forward return days (default 5)."},
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
        forward_days = int(kwargs.get("forward_days", 5))

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return _err(f"db open failed: {exc}")

        if conn is None:
            return _err("workspace has no DuckDB")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, close FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return _err(f"ohlcv query failed: {exc}")

        if prices_df.empty:
            return _err("ohlcv table is empty")

        available_assets = sorted(prices_df["asset"].unique())
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return _err(f"asset '{asset}' not found")

        asset_df = prices_df[prices_df["asset"] == asset].copy()
        asset_df = asset_df.set_index("date")[["close"]]
        asset_df = asset_df.sort_index()

        try:
            factor_series = compute_factor(factor_code, asset_df)
        except Exception as exc:  # noqa: BLE001
            return _err(f"compute failed: {exc}")

        # Compute forward returns
        asset_df["fwd_ret"] = asset_df["close"].pct_change(forward_days).shift(-forward_days)

        # Align and compute IC
        import pandas as pd
        aligned = pd.concat([factor_series, asset_df["fwd_ret"]], axis=1).dropna()
        if len(aligned) < 10:
            return _err("insufficient data for IC analysis (need >= 10 rows)")

        ic = aligned.iloc[:, 0].corr(aligned["fwd_ret"])
        ic_mean = float(aligned.iloc[:, 0].corr(aligned["fwd_ret"], method="spearman")) if len(aligned) > 5 else 0.0

        return _ok({
            "factor_code": factor_code,
            "asset": asset,
            "forward_days": forward_days,
            "ic_mean": round(ic, 4) if pd.notna(ic) else None,
            "spearman_ic": round(ic_mean, 4),
            "n_observations": len(aligned),
        })


# ── 8. PatternRecognitionTool ──────────────────────────────────────


class PatternRecognitionTool(BaseTool):
    """Detect common chart patterns in price data."""

    name = "pattern_recognition"
    description = (
        "Detect common chart patterns (head-shoulders, double-top/bottom, "
        "trend lines, support/resistance) in price data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "asset": {"type": "string", "description": "Asset code."},
            "lookback": {"type": "integer", "description": "Days to look back (default 60)."},
        },
        "required": ["workspace"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        asset = kwargs.get("asset")
        lookback = int(kwargs.get("lookback", 60))

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return _err(f"db open failed: {exc}")

        if conn is None:
            return _err("workspace has no DuckDB")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume FROM ohlcv ORDER BY date"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return _err(f"ohlcv query failed: {exc}")

        if prices_df.empty:
            return _err("ohlcv table is empty")

        if asset:
            prices_df = prices_df[prices_df["asset"] == asset]

        prices_df = prices_df.tail(lookback)
        if len(prices_df) < 10:
            return _err("insufficient data")

        closes = prices_df["close"].values
        highs = prices_df["high"].values
        lows = prices_df["low"].values

        patterns = []

        # Simple trend detection
        if len(closes) >= 20:
            ma20 = closes[-20:].mean()
            ma5 = closes[-5:].mean() if len(closes) >= 5 else ma20
            if ma5 > ma20:
                patterns.append({"pattern": "uptrend", "confidence": 0.6})
            elif ma5 < ma20:
                patterns.append({"pattern": "downtrend", "confidence": 0.6})

        # Support/Resistance
        recent_high = float(highs.max())
        recent_low = float(lows.min())
        current = float(closes[-1])
        range_pct = (recent_high - recent_low) / recent_high * 100 if recent_high > 0 else 0

        if current >= recent_high * 0.98:
            patterns.append({"pattern": "near_resistance", "level": round(recent_high, 2), "confidence": 0.5})
        if current <= recent_low * 1.02:
            patterns.append({"pattern": "near_support", "level": round(recent_low, 2), "confidence": 0.5})

        # Volatility squeeze
        if len(closes) >= 20:
            std20 = float(closes[-20:].std())
            std5 = float(closes[-5:].std()) if len(closes) >= 5 else std20
            if std5 < std20 * 0.6:
                patterns.append({"pattern": "volatility_squeeze", "confidence": 0.5})

        return _ok({
            "asset": asset or "(all)",
            "lookback": lookback,
            "current_price": round(current, 2),
            "range_pct": round(range_pct, 2),
            "patterns": patterns,
        })


# ── 9. ListSkillsTool ─────────────────────────────────────────────


class ListSkillsTool(BaseTool):
    """List available skills (name + one-line description)."""

    name = "list_skills"
    description = (
        "List all available methodology skills. Returns skill names, categories, "
        "and one-line descriptions. Use load_skill to get full content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "category": {"type": "string", "description": "Filter by category."},
        },
        "required": ["workspace"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        category = kwargs.get("category")

        try:
            from ...skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first, then bundled templates
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            if category:
                skills = registry.by_category(category)
            else:
                skills = registry.list_all()

            skill_list = [
                {
                    "name": s.name,
                    "category": s.category,
                    "description": s.description[:120] if s.description else "",
                }
                for s in skills
            ]

            return _ok({
                "n_skills": len(skill_list),
                "categories": registry.categories(),
                "skills": skill_list,
            })
        except Exception as exc:  # noqa: BLE001
            return _err(f"list_skills failed: {exc}")


# ── 10. LoadSkillTool ─────────────────────────────────────────────


class LoadSkillTool(BaseTool):
    """Load full skill content by name."""

    name = "load_skill"
    description = (
        "Load a skill's full content by name. Returns the complete markdown "
        "documentation including API contracts, workflows, and examples."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "name": {"type": "string", "description": "Skill name to load."},
        },
        "required": ["workspace", "name"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            workspace = _workspace_from_kwargs(kwargs)
        except ValueError as exc:
            return _err(str(exc))

        name = kwargs.get("name")
        if not isinstance(name, str) or not name:
            return _err("missing or invalid 'name'")

        try:
            from ...skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first (user overrides), then bundled
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            skill = registry.get(name)
            if skill is None:
                available = [s.name for s in registry.list_all()][:20]
                return _err(
                    f"skill '{name}' not found",
                    available=available,
                )

            return _ok({
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "tags": skill.tags,
                "content": skill.content,
            })
        except Exception as exc:  # noqa: BLE001
            return _err(f"load_skill failed: {exc}")


# ── 11. OptionsPricingTool ──────────────────────────────────────────


class OptionsPricingTool(BaseTool):
    """Black-Scholes options pricing with Greeks."""

    name = "options_pricing"
    description = (
        "Compute Black-Scholes option price and Greeks (delta, gamma, theta, vega, rho)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "spot": {"type": "number", "description": "Current spot price."},
            "strike": {"type": "number", "description": "Strike price."},
            "rate": {"type": "number", "description": "Risk-free rate (annualized, e.g. 0.05)."},
            "volatility": {"type": "number", "description": "Volatility (annualized, e.g. 0.2)."},
            "time_to_expiry": {"type": "number", "description": "Time to expiry in years (e.g. 0.5)."},
            "option_type": {"type": "string", "description": "'call' or 'put'."},
        },
        "required": ["spot", "strike", "rate", "volatility", "time_to_expiry", "option_type"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            spot = float(kwargs["spot"])
            strike = float(kwargs["strike"])
            rate = float(kwargs["rate"])
            vol = float(kwargs["volatility"])
            T = float(kwargs["time_to_expiry"])
            option_type = kwargs.get("option_type", "call").lower()
        except (KeyError, ValueError, TypeError) as exc:
            return _err(f"invalid parameters: {exc}")

        if option_type not in ("call", "put"):
            return _err("option_type must be 'call' or 'put'")
        if T <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
            return _err("spot, strike, volatility, and time_to_expiry must be positive")

        from math import exp, log, sqrt

        from scipy.stats import norm

        d1 = (log(spot / strike) + (rate + 0.5 * vol**2) * T) / (vol * sqrt(T))
        d2 = d1 - vol * sqrt(T)

        if option_type == "call":
            price = spot * norm.cdf(d1) - strike * exp(-rate * T) * norm.cdf(d2)
            delta = float(norm.cdf(d1))
        else:
            price = strike * exp(-rate * T) * norm.cdf(-d2) - spot * norm.cdf(-d1)
            delta = float(norm.cdf(d1) - 1)

        gamma = float(norm.pdf(d1) / (spot * vol * sqrt(T)))
        theta = float(
            -(spot * norm.pdf(d1) * vol) / (2 * sqrt(T))
            - rate * strike * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2)
        )
        vega = float(spot * norm.pdf(d1) * sqrt(T) / 100)
        rho = float(
            strike * T * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2) / 100
        )

        return _ok({
            "option_type": option_type,
            "spot": spot,
            "strike": strike,
            "rate": rate,
            "volatility": vol,
            "time_to_expiry": T,
            "price": round(price, 4),
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        })


# ── Registry ─────────────────────────────────────────────────────────


def build_default_registry() -> ToolRegistry:
    """Build a ToolRegistry with all tools.

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
    r.register(FactorAnalysisTool())
    r.register(PatternRecognitionTool())
    r.register(ListSkillsTool())
    r.register(LoadSkillTool())
    r.register(OptionsPricingTool())
    # Phase 2: Web I/O tools (conditional on dependencies)
    try:
        from .web_tools import register_web_tools
        register_web_tools(r)
    except Exception:
        pass
    # Phase 3: Market data tools
    try:
        from .data_tools import register_data_tools
        register_data_tools(r)
    except Exception:
        pass
    return r


__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "RunBacktestTool",
    "ComputeFactorTool",
    "GitDiffTool",
    "ListHistoryTool",
    "FactorAnalysisTool",
    "PatternRecognitionTool",
    "ListSkillsTool",
    "LoadSkillTool",
    "OptionsPricingTool",
    "build_default_registry",
]
