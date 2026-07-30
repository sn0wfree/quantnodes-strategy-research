"""Agent tools for market data: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import json
import logging
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
        try:
            max_rows = safe_get_param(kwargs, "max_rows", int, default=500)
        except TypeError:
            max_rows = 500

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

            # Truncate and serialize
            result_data = {}
            total_rows = 0
            truncated = False
            for code, df in data.items():
                if df is None or df.empty:
                    result_data[code] = []
                    continue
                rows = df.tail(max_rows).reset_index()
                n_rows = len(rows)
                total_rows += n_rows
                if n_rows > max_rows:
                    truncated = True
                # Convert to records
                records = []
                for _, row in rows.iterrows():
                    record = {}
                    for col in rows.columns:
                        val = row[col]
                        if hasattr(val, "isoformat"):
                            record[col] = val.isoformat()
                        elif hasattr(val, "item"):
                            record[col] = val.item()
                        else:
                            record[col] = val
                    records.append(record)
                result_data[code] = records

            return _ok({
                "data": result_data,
                "meta": {
                    "codes": codes,
                    "start_date": start_date,
                    "end_date": end_date,
                    "interval": interval,
                    "source": effective_source,
                    "total_rows": total_rows,
                    "max_rows_per_code": max_rows,
                    "truncated": truncated,
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
        "After calling get_market_data, use this tool to persist the data "
        "so factor analysis tools can access it via the ohlcv table."
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
                    "then call import_data(data=<result.data>)"
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
                    "then call import_data(data=<result.data>)"
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
                                f"call get_market_data(codes=[{code!r}], "
                                "start_date='2023-01-01', end_date='2023-12-31') first, "
                                "then pass result.data as the data argument"
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


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (GetMarketDataTool, ListDataSourcesTool, SearchSymbolTool, ImportDataTool):
        registry.register(tool_cls())
