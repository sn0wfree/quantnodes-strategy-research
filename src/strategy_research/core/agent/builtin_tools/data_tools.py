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
        from ...core.data_source.base import validate_date_range
        from ...core.data_source.registry import (
            LOADER_REGISTRY,
            NoAvailableSourceError,
            resolve_loader,
        )
        from ...core.data_source.utils import detect_market

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
        from ...core.data_source.registry import LOADER_REGISTRY, _ensure_registered

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


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (GetMarketDataTool, ListDataSourcesTool, SearchSymbolTool):
        registry.register(tool_cls())
