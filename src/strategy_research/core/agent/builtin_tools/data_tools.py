"""Agent tools for market data: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..tools import BaseTool, ToolRegistry

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

        codes = kwargs.get("codes", [])
        start_date = kwargs.get("start_date", "")
        end_date = kwargs.get("end_date", "")
        interval = kwargs.get("interval", "1D")
        source = kwargs.get("source")
        max_rows = int(kwargs.get("max_rows", 500))

        if not codes:
            return _err("codes is required and must be non-empty")
        if not start_date or not end_date:
            return _err("start_date and end_date are required")

        try:
            validate_date_range(start_date, end_date)
        except ValueError as exc:
            return _err(str(exc))

        try:
            if source and source in LOADER_REGISTRY:
                loader = LOADER_REGISTRY[source]()
                if not loader.is_available():
                    return _err(f"source '{source}' is not available")
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
            return _err(f"no available data source: {exc}")
        except Exception as exc:
            logger.exception("get_market_data failed")
            return _err(f"fetch failed: {exc}")


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
        limit = int(kwargs.get("limit", 10))

        if not query:
            return _err("query is required")

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
            return _err("akshare not installed. Install with: pip install akshare")
        except Exception as exc:
            logger.warning("search_symbol failed for %r: %s", query, exc)
            return _err(f"search failed: {exc}")


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
            return _err("missing required kwarg 'workspace'")

        data = kwargs.get("data")
        if not data or not isinstance(data, dict):
            return _err("missing or invalid 'data' (expect dict from get_market_data)")

        strategy_name = kwargs.get("strategy_name", "default")

        try:
            from pathlib import Path
            import pandas as pd
            from ...db import get_connection, init_db

            ws = Path(workspace)
            init_db(ws)

            conn = get_connection(ws)
            if conn is None:
                return _err("failed to open DuckDB")

            # Defensive unwrap: some LLMs (notably MiniMax-M3) wrap the
            # per-code records list in a single-key object like
            # {"item": [...]} or {"data": [...]}. We try common wrapper
            # keys before falling through to a clear actionable error.
            _LIST_WRAPPER_KEYS = (
                "item", "data", "records", "bars", "rows", "ohlcv", "values",
            )

            total_rows = 0
            for code, records in data.items():
                if isinstance(records, dict):
                    unwrapped = None
                    for key in _LIST_WRAPPER_KEYS:
                        if key in records and isinstance(records[key], list):
                            unwrapped = records[key]
                            logger.debug(
                                "import_data: unwrapped data[%r][%r]", code, key,
                            )
                            break
                    if unwrapped is None:
                        return _err(
                            f"data[{code!r}] is a dict (length {len(records)}) but "
                            f"contains no list of records. Got keys: "
                            f"{list(records.keys())[:5]}. "
                            f"Expected: data[{code!r}] = "
                            f"[{{'trade_date': '...', 'close': ...}}, ...]. "
                            f"Fix: call get_market_data(codes=[{code!r}], "
                            f"start_date='2023-01-01', end_date='2023-12-31') "
                            f"first, then pass result.data as the data argument."
                        )
                    records = unwrapped

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
                    return _err(f"no date column in data for {code}")

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
            return _err(f"import failed: {exc}")


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (GetMarketDataTool, ListDataSourcesTool, SearchSymbolTool, ImportDataTool):
        registry.register(tool_cls())
