"""Agent tools for market data: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..tools import BaseTool, ToolRegistry
from .utils import err_actionable, safe_get_param, try_unwrap_list

logger = logging.getLogger(__name__)


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps(
        {"status": "error", "error": str(message), **extra},
        ensure_ascii=False,
    )


# ── 1. GetMarketDataTool ────────────────────────────────────────


class GetMarketDataTool(BaseTool):
    """Fetch OHLCV market data using the loader fallback chain."""

    name = "get_market_data"
    description = (
        "Fetch OHLCV market data for given codes using the data source fallback chain. "
        "Auto-detects market type (A-share, US, HK, crypto, etc.) and selects the "
        "best available loader."
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of asset codes (e.g. ['000001.SZ', '600519.SH']).",
            },
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)."},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)."},
            "interval": {"type": "string", "description": "K-line interval (default '1D').", "default": "1D"},
            "source": {"type": "string", "description": "Optional data source override (e.g. 'tushare')."},
            "max_rows": {"type": "integer", "description": "Max rows per code (default 500).", "default": 500},
        },
        "required": ["codes", "start_date", "end_date"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        from ...data_source.base import validate_date_range
        from ...data_source.registry import (
            LOADER_REGISTRY,
            NoAvailableSourceError,
            resolve_loader,
        )
        from ...data_source.utils import detect_market

        # Defensive reads: LLM may stringify codes as JSON, or pass them
        # as a single string "A,B,C", or wrap them in a dict.
        try:
            codes = safe_get_param(kwargs, "codes", list)
        except TypeError as exc:
            # Fall back: maybe the LLM passed "A,B,C" as a single string
            raw_codes = kwargs.get("codes")
            if isinstance(raw_codes, str):
                codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
            else:
                return err_actionable(
                    f"codes parameter has wrong shape: {exc}",
                    received=raw_codes,
                    expected="list[str] of asset codes, e.g. ['000001.SZ', '600519.SH']",
                    fix="pass codes as a JSON array string OR a list, e.g. "
                        "codes=['000001.SZ', '600519.SH']",
                    tool="get_market_data",
                )

        start_date = kwargs.get("start_date", "")
        end_date = kwargs.get("end_date", "")
        interval = kwargs.get("interval", "1D")
        source = kwargs.get("source")

        if not codes:
            return err_actionable(
                "codes is required and must be non-empty",
                expected="list[str] of asset codes, e.g. ['000001.SZ', '600519.SH']",
                fix="pass at least one code, e.g. codes=['600519.SH']",
                tool="get_market_data",
            )
        if not start_date or not end_date:
            return err_actionable(
                "start_date and end_date are required",
                expected="ISO date strings, e.g. start_date='2023-01-01'",
                fix="pass both, e.g. start_date='2023-01-01', end_date='2023-12-31'",
                tool="get_market_data",
            )

        try:
            validate_date_range(start_date, end_date)
        except ValueError as exc:
            return err_actionable(
                str(exc),
                received={"start_date": start_date, "end_date": end_date},
                expected="valid date range, e.g. start_date='2023-01-01', end_date='2023-12-31'",
                fix="ensure start_date is before end_date and both are valid ISO dates",
                tool="get_market_data",
            )

        try:
            if source and source in LOADER_REGISTRY:
                loader = LOADER_REGISTRY[source]()
                if not loader.is_available():
                    return err_actionable(
                        f"source '{source}' is not available",
                        received=source,
                        expected="one of " + ", ".join(sorted(LOADER_REGISTRY.keys())),
                        fix="either omit `source` to use auto-detection, or pick an available one",
                        tool="get_market_data",
                    )
                effective_source = source
            else:
                market = detect_market(codes[0])
                loader = resolve_loader(market)
                effective_source = loader.name

            data = loader.fetch(codes, start_date, end_date, interval=interval)

            # Write full OHLCV to the loader parquet cache and return a
            # compact summary — the full rows must NOT enter the LLM prompt
            # (that was the context-overflow root cause; see
            # docs/context-overflow-fix.md). A separate commit_market_data
            # tool merges the cached parquet into DuckDB after the agent
            # has evaluated the summary.
            from ...data_source.cache import cache_put, make_cache_key

            cached: dict[str, str] = {}
            summary: dict[str, Any] = {}
            preview: dict[str, Any] = {}
            total_rows = 0
            for code, df in data.items():
                if df is None or df.empty:
                    summary[code] = {"rows": 0, "status": "empty"}
                    continue
                key = make_cache_key(
                    effective_source, code, interval, start_date, end_date
                )
                cache_put(key, df)
                cached[code] = key
                n_rows = len(df)
                total_rows += n_rows
                # Per-code summary (stats only, not the rows themselves)
                close = df["close"] if "close" in df.columns else None
                volume = df["volume"] if "volume" in df.columns else None
                s = {
                    "rows": n_rows,
                    "status": "ok",
                    "cache_key": key,
                }
                if close is not None and not close.empty:
                    s["first_close"] = float(close.iloc[0])
                    s["last_close"] = float(close.iloc[-1])
                    s["close_min"] = float(close.min())
                    s["close_max"] = float(close.max())
                if volume is not None and not volume.empty:
                    s["avg_volume"] = float(volume.mean())
                summary[code] = s
                # Small preview: first 5 rows (date + OHLCV)
                preview_rows = []
                for _, row in df.head(5).iterrows():
                    rec: dict[str, Any] = {}
                    for col in df.columns:
                        val = row[col]
                        if hasattr(val, "isoformat"):
                            rec[str(col)] = val.isoformat()
                        elif hasattr(val, "item"):
                            rec[str(col)] = val.item()
                        else:
                            rec[str(col)] = val
                    preview_rows.append(rec)
                preview[code] = preview_rows

            return _ok({
                "cached": cached,
                "summary": summary,
                "preview": preview,
                "meta": {
                    "codes": codes,
                    "start_date": start_date,
                    "end_date": end_date,
                    "interval": interval,
                    "source": effective_source,
                    "total_rows": total_rows,
                    "cache_dir": str(Path.home() / ".quantnodes-research" / "loader_cache"),
                    "note": (
                        "行情已写入 loader parquet 缓存，未进入对话上下文。"
                        "请先评估 summary/preview 的数据质量，然后调用 "
                        "commit_market_data(codes=[...], cache_keys=[...], "
                        "strategy_name=..., workspace=...) 将数据合并入 DuckDB "
                        "供回测使用。"
                    ),
                },
            })

        except NoAvailableSourceError as exc:
            return err_actionable(
                f"no available data source: {exc}",
                received=kwargs.get("codes"),
                expected="list of asset codes with a registered data source",
                fix="use list_data_sources() to see what's available, or check your network",
                tool="get_market_data",
            )
        except Exception as exc:
            logger.exception("get_market_data failed")
            return err_actionable(
                f"fetch failed: {exc}",
                received={"codes": codes, "start_date": start_date, "end_date": end_date},
                expected="valid codes + date range",
                fix="verify codes are correct, dates are valid, and a data source is registered",
                tool="get_market_data",
            )


# ── 2. ListDataSourcesTool ──────────────────────────────────────


class ListDataSourcesTool(BaseTool):
    """List available data sources and their status."""

    name = "list_data_sources"
    description = (
        "List all registered data sources, showing which are available "
        "and which require API keys."
    )
    parameters = {
        "type": "object",
        "properties": {},
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        from ...data_source.registry import LOADER_REGISTRY, _ensure_registered

        _ensure_registered()
        sources = []
        for name, cls in LOADER_REGISTRY.items():
            try:
                instance = cls()
                available = instance.is_available()
                markets = list(getattr(instance, "markets", set()))
                requires_auth = getattr(instance, "requires_auth", False)
            except Exception:
                available = False
                markets = []
                requires_auth = False
            sources.append({
                "name": name,
                "available": available,
                "markets": markets,
                "requires_auth": requires_auth,
            })

        return _ok({
            "n_sources": len(sources),
            "sources": sources,
        })


# ── 3. SearchSymbolTool ─────────────────────────────────────────


class SearchSymbolTool(BaseTool):
    """Search for stock/fund symbols by name or code."""

    name = "search_symbol"
    description = (
        "Search for stock or fund symbols by name or code. "
        "Primarily supports A-share market via akshare."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (name or code)."},
            "market": {"type": "string", "description": "Market filter (default 'a_share').", "default": "a_share"},
            "limit": {"type": "integer", "description": "Max results (default 10).", "default": 10},
        },
        "required": ["query"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        market = kwargs.get("market", "a_share")
        try:
            limit = safe_get_param(kwargs, "limit", int, default=10)
        except TypeError:
            limit = 10

        if not query:
            return err_actionable(
                "query is required",
                expected="non-empty string, e.g. 'maotai' or '600519'",
                fix="pass a non-empty query, e.g. query='maotai'",
                tool="search_symbol",
            )

        try:
            import akshare as ak
            # A-share: use spot data for fuzzy search
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return _ok({"results": [], "query": query, "market": market})

            # Fuzzy match: query in code or name
            mask = (
                df["代码"].str.contains(query, case=False, na=False)
                | df["名称"].str.contains(query, case=False, na=False)
            )
            matched = df[mask].head(limit)

            results = []
            for _, row in matched.iterrows():
                results.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "market": "a_share",
                    "price": row.get("最新价"),
                    "change_pct": row.get("涨跌幅"),
                })

            return _ok({
                "results": results,
                "query": query,
                "market": market,
                "limit": limit,
                "n_results": len(results),
            })

        except ImportError:
            return err_actionable(
                "akshare not installed",
                fix="install with: pip install akshare",
                tool="search_symbol",
            )
        except Exception as exc:
            logger.warning("search_symbol failed for %r: %s", query, exc)
            return err_actionable(
                f"search failed: {exc}",
                received=query,
                fix="try a different query, or check that akshare is installed and network is up",
                tool="search_symbol",
            )


# ── 4. ImportDataTool ────────────────────────────────────────


class ImportDataTool(BaseTool):
    """Import OHLCV data into the workspace DuckDB for factor analysis."""

    name = "import_data"
    description = (
        "Import OHLCV market data into the workspace DuckDB. "
        "Note: get_market_data now writes data to the parquet cache "
        "automatically — the recommended flow is "
        "get_market_data → commit_market_data(cache_keys=...) → run_backtest. "
        "import_data is for manual/external data (e.g. pasted records or CSV)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "data": {
                "type": "object",
                "description": (
                    "OHLCV data dict from get_market_data. "
                    "Format: {asset_code: [records]}. Each record has "
                    "'trade_date' (or 'date') + OHLCV fields. "
                    "Example: {'600519.SH': [{'trade_date': '2023-12-11', "
                    "'close': 1544.555, 'open': 1536.555, 'high': 1550.555, "
                    "'low': 1503.555, 'volume': 36831.0}, ...]}"
                ),
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "strategy_name": {
                "type": "string",
                "description": "Strategy name for data partitioning (default: 'default').",
                "default": "default",
            },
        },
        "required": ["workspace", "data"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        workspace = kwargs.get("workspace")
        if not workspace:
            return err_actionable(
                "missing required parameter 'workspace'",
                expected="absolute path to workspace root, e.g. '/home/user/research'",
                fix="set workspace='/path/to/your/workspace'",
                tool="import_data",
            )

        # Defensive read: data may be stringified JSON, or wrapped in a dict.
        try:
            data = safe_get_param(kwargs, "data", dict)
        except TypeError as exc:
            return err_actionable(
                f"data parameter has wrong shape: {exc}",
                received=kwargs.get("data"),
                expected="dict[asset_code, list[record]] — output of get_market_data(data field)",
                fix=(
                    "call get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31') first, "
                    "then call commit_market_data(cache_keys=[...]) to merge into DuckDB"
                ),
                tool="import_data",
            )

        if not data:
            return err_actionable(
                "missing or invalid 'data' (expect dict from get_market_data)",
                expected="non-empty dict, e.g. {'600519.SH': [{'trade_date': '2023-12-11', 'close': 1544.555, ...}, ...]}",
                fix=(
                    "call get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31') first, "
                    "then call commit_market_data(cache_keys=[...]) to merge into DuckDB"
                ),
                tool="import_data",
            )

        strategy_name = kwargs.get("strategy_name", "default")

        try:
            from pathlib import Path
            import pandas as pd
            from ...db import get_connection, init_db

            ws = Path(workspace)
            init_db(ws)

            conn = get_connection(ws)
            if conn is None:
                return err_actionable(
                    "failed to open DuckDB",
                    received=str(workspace),
                    expected="writable workspace path",
                    fix="run `quantnodes-research init` first, or check workspace permissions",
                    tool="import_data",
                )

            # Defensive unwrap per-code: LLM may wrap records in single-key
            # object like {"item": [...]} or {"data": [...]}.
            total_rows = 0
            for code, records in data.items():
                if isinstance(records, dict):
                    unwrapped = try_unwrap_list(records)
                    if unwrapped is None:
                        return err_actionable(
                            f"data[{code!r}] is a dict but contains no list of records",
                            received=records,
                            expected=(
                                f"data[{code!r}] = [{{'trade_date': '2023-12-11', "
                                "'close': 1544.555, ...}}, ...]"
                            ),
                fix=(
                    "call get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31') first, "
                    "then call commit_market_data(cache_keys=[...]) to merge into DuckDB"
                ),
                tool="import_data",
            )
                    records = unwrapped
                    logger.debug("import_data: unwrapped data[%r]", code)

                if not records:
                    continue
                df = pd.DataFrame(records)
                if df.empty:
                    continue

                # Normalize column names
                col_map = {}
                for col in df.columns:
                    cl = col.lower()
                    if cl in ("trade_date", "tradedate", "datetime"):
                        col_map[col] = "date"
                    elif cl in ("code", "symbol", "ticker"):
                        col_map[col] = "asset_code"
                if col_map:
                    df = df.rename(columns=col_map)

                # Ensure required columns
                if "asset_code" not in df.columns:
                    df["asset_code"] = code
                if "date" not in df.columns:
                    return err_actionable(
                        f"data[{code!r}] has no 'date' or 'trade_date' column",
                        received=list(df.columns),
                        expected="records with 'trade_date' (or 'date') + OHLCV fields",
                        fix="ensure data comes from get_market_data, which produces "
                            "{trade_date, open, high, low, close, volume} records",
                        tool="import_data",
                    )

                # Fill missing OHLCV columns
                for c in ("open", "high", "low", "close", "volume"):
                    if c not in df.columns:
                        df[c] = df["close"] if c != "volume" else 0.0

                df["strategy_name"] = strategy_name
                df = df[["strategy_name", "asset_code", "date", "open", "high", "low", "close", "volume"]]

                conn.execute("""
                    INSERT OR REPLACE INTO price_data
                    (strategy_name, asset_code, date, open, high, low, close, volume)
                    SELECT strategy_name, asset_code, date, open, high, low, close, volume
                    FROM df
                """)
                total_rows += len(df)

            conn.close()
            return _ok({
                "imported": total_rows,
                "n_codes": len(data),
                "strategy_name": strategy_name,
                "message": f"Imported {total_rows} rows from {len(data)} codes into ohlcv table",
            })

        except Exception as exc:
            logger.exception("import_data failed")
            return err_actionable(
                f"import failed: {exc}",
                received=str(kwargs.get("data"))[:200],
                expected="dict[asset_code, list[record]] from get_market_data",
                fix="verify data shape and workspace is writable",
                tool="import_data",
            )


# ── 5. CommitMarketDataTool ───────────────────────────────────────


class CommitMarketDataTool(BaseTool):
    """Merge cached market data (parquet) into the workspace DuckDB.

    get_market_data writes OHLCV to the loader parquet cache and returns
    a compact summary (never the full rows). After the agent evaluates
    the summary, this tool reads the cached parquet by cache_key and
    writes it into the DuckDB price_data table so backtests / factor
    tools can use it. This keeps large market data out of the LLM prompt.
    """

    name = "commit_market_data"
    description = (
        "Merge previously cached OHLCV market data into the workspace "
        "DuckDB. Call after get_market_data to persist the data for "
        "backtest/factor analysis. Requires the cache_keys returned by "
        "get_market_data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Workspace root path."},
            "cache_keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Cache keys returned by get_market_data "
                    "(meta.cached[code] / summary[code].cache_key)."
                ),
            },
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Asset codes (parallel to cache_keys).",
            },
            "strategy_name": {
                "type": "string",
                "description": "Strategy name for data partitioning (default 'default').",
                "default": "default",
            },
        },
        "required": ["workspace", "cache_keys", "codes"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        workspace = kwargs.get("workspace")
        if not workspace:
            return err_actionable(
                "missing required parameter 'workspace'",
                expected="absolute path to workspace root, e.g. '/home/user/research'",
                fix="set workspace='/path/to/your/workspace'",
                tool="commit_market_data",
            )

        try:
            cache_keys = safe_get_param(kwargs, "cache_keys", list)
            codes = safe_get_param(kwargs, "codes", list)
        except TypeError as exc:
            return err_actionable(
                f"cache_keys/codes parameter has wrong shape: {exc}",
                received=kwargs.get("cache_keys"),
                expected="list of cache keys from get_market_data",
                fix="pass cache_keys=[...] and codes=[...] exactly as returned",
                tool="commit_market_data",
            )

        if not cache_keys or not codes or len(cache_keys) != len(codes):
            return err_actionable(
                "cache_keys and codes must be non-empty and equal length",
                received={"cache_keys": cache_keys, "codes": codes},
                expected="one cache_key per code, e.g. cache_keys=['abc123'], codes=['600519.SH']",
                fix="re-run get_market_data and copy the returned cached/cache_key values",
                tool="commit_market_data",
            )

        strategy_name = kwargs.get("strategy_name", "default")

        try:
            from ...data_source.cache import _cache_root
            import pandas as pd
            from ...db import get_connection, init_db

            ws = Path(workspace)
            init_db(ws)
            conn = get_connection(ws)
            if conn is None:
                return err_actionable(
                    "failed to open DuckDB",
                    received=str(workspace),
                    expected="writable workspace path",
                    fix="run `quantnodes-research init` first, or check workspace permissions",
                    tool="commit_market_data",
                )

            cache_root = _cache_root()
            total_rows = 0
            committed = []
            missing = []
            for code, key in zip(codes, cache_keys):
                path = cache_root / f"{key}.parquet"
                if not path.exists():
                    missing.append(code)
                    continue
                try:
                    df = pd.read_parquet(path)
                except Exception:  # noqa: BLE001
                    logger.exception("commit: parquet read failed for %s", code)
                    missing.append(code)
                    continue
                if df.empty:
                    continue

                # Normalize: index or 'date'/'trade_date' column
                if "date" not in df.columns:
                    if df.index is not None and getattr(df.index, "name", None) in (
                        "date", "trade_date", "datetime",
                    ):
                        df = df.reset_index()
                    elif isinstance(df.index, pd.DatetimeIndex):
                        df = df.reset_index()
                col_map = {}
                for col in df.columns:
                    cl = str(col).lower()
                    if cl in ("trade_date", "tradedate", "datetime"):
                        col_map[col] = "date"
                    elif cl in ("code", "symbol", "ticker"):
                        col_map[col] = "asset_code"
                if col_map:
                    df = df.rename(columns=col_map)
                if "asset_code" not in df.columns:
                    df["asset_code"] = code
                if "date" not in df.columns:
                    return err_actionable(
                        f"cached parquet for {code} has no date column",
                        received=list(df.columns),
                        expected="parquet with date/trade_date + OHLCV columns",
                        fix="re-fetch via get_market_data",
                        tool="commit_market_data",
                    )
                for c in ("open", "high", "low", "close", "volume"):
                    if c not in df.columns:
                        df[c] = df["close"] if c != "volume" else 0.0
                df["strategy_name"] = strategy_name
                df = df[["strategy_name", "asset_code", "date",
                         "open", "high", "low", "close", "volume"]]

                conn.execute("""
                    INSERT OR REPLACE INTO price_data
                    (strategy_name, asset_code, date, open, high, low, close, volume)
                    SELECT strategy_name, asset_code, date, open, high, low, close, volume
                    FROM df
                """)
                total_rows += len(df)
                committed.append({"code": code, "rows": len(df)})

            conn.close()
            return _ok({
                "committed": committed,
                "total_rows": total_rows,
                "missing": missing,
                "strategy_name": strategy_name,
                "next_step": f"run_backtest(strategy_name='{strategy_name}', workspace='{workspace}')",
                "message": f"Committed {total_rows} rows for {len(committed)} codes into price_data"
                           + (f"; missing {len(missing)}: {missing}" if missing else ""),
            })

        except Exception as exc:
            logger.exception("commit_market_data failed")
            return err_actionable(
                f"commit failed: {exc}",
                received={"cache_keys": cache_keys, "codes": codes},
                expected="valid cache keys from get_market_data + writable workspace",
                fix="verify cache_keys are from get_market_data and workspace is writable",
                tool="commit_market_data",
            )


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (
        GetMarketDataTool,
        ListDataSourcesTool,
        SearchSymbolTool,
        ImportDataTool,
        CommitMarketDataTool,
    ):
        registry.register(tool_cls())
