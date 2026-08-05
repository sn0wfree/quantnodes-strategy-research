"""Agent tools for market data: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..tools import BaseTool, EFFECT_DB, EFFECT_FS, EFFECT_NET, ToolContext, ToolRegistry
from .utils import err_actionable, try_unwrap_dict, try_unwrap_list

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
    """获取 OHLCV 行情并持久化到工作区 DuckDB（一步完成）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
    #
    # ## 用途
    # 按 fallback 链获取 OHLCV 行情, persist=True (默认) 直接写入
    # DuckDB price_data (回测/因子立即可用), 返回摘要+预览;
    # 全量数据不进 LLM prompt (context 安全)。
    #
    # ## 参数
    # - codes: 资产代码列表 (必填, 如 ['600519.SH','000858.SZ'])
    # - start_date/end_date: ISO 日期 (必填)
    # - interval: K 线周期 (默认 '1D')
    # - source: 数据源覆盖 (可选)
    # - max_rows: 每代码最大行数 (默认 500)
    # - persist: 是否入库 (默认 True; False 只查看)
    # - strategy_name: 数据分区名 (默认 'default')
    # - force_refresh: 跳过缓存强制网络取数 (默认 False)
    #
    # ## 示例
    # {"codes": ["600519.SH"], "start_date": "2023-01-01", "end_date": "2023-12-31"}
    #
    # ## 边界
    # 写工具 (effects: db + net); 幂等 (INSERT OR REPLACE);
    # 纯数字代码会误判为 FRED/macro, A 股务必带后缀。
    #
    # ## 错误处理范式
    # - 缺 codes/日期 → error + expected 示例
    # - 日期范围非法 → error + 校验说明
    # - 指定 source 不可用 → error + 可用源列表
    # - 网络失败 → error (transient, 可重试)
    # - persist=True 幂等, 重试安全
    #
    # ## 相关工具
    # run_backtest/compute_factor/factor_*: 数据消费方
    # ─────────────────────────────────────────────
    """

    name = "get_market_data"
    description = (
        "获取 OHLCV 行情, persist=True (默认) 一步写入 DuckDB; 返回摘要+预览, "
        "全量数据不进 prompt。"
    )
    repeatable = True
    category = "行情"
    effects = frozenset({EFFECT_DB, EFFECT_NET})

    def execute(
        self,
        ctx: ToolContext,
        codes: list[str],
        start_date: str,
        end_date: str,
        interval: str = "1D",
        source: str | None = None,
        max_rows: int = 500,
        persist: bool = True,
        strategy_name: str = "default",
        force_refresh: bool = False,
    ) -> str:
        from ...data_source.base import validate_date_range
        from ...data_source.registry import (
            LOADER_REGISTRY,
            NoAvailableSourceError,
            resolve_loader,
        )
        from ...data_source.utils import detect_market

        # Defensive reads: framework coercion handles JSON-string lists and
        # single-key wrapping; "A,B,C" comma strings are split here.
        raw_codes = codes
        if isinstance(raw_codes, str):
            codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
        elif not isinstance(raw_codes, list):
            return err_actionable(
                f"codes parameter has wrong shape: {type(raw_codes).__name__}",
                received=raw_codes,
                expected="list[str] of asset codes, e.g. ['000001.SZ', '600519.SH']",
                fix="pass codes as a JSON array string OR a list, e.g. "
                    "codes=['000001.SZ', '600519.SH']",
                tool="get_market_data",
            )

        strategy_name = strategy_name or "default"
        workspace = ctx.workspace
        if persist and workspace is None:
            return err_actionable(
                "missing workspace context (required when persist=True)",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="get_market_data",
            )

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

            data = loader.fetch(codes, start_date, end_date, interval=interval,
                               force_refresh=force_refresh)

            # Build compact summary + small preview. The full rows must NOT
            # enter the LLM prompt (context-overflow root cause; see
            # docs/context-overflow-fix.md).
            summary: dict[str, Any] = {}
            preview: dict[str, Any] = {}
            total_rows = 0
            for code, df in data.items():
                if df is None or df.empty:
                    summary[code] = {"rows": 0, "status": "empty"}
                    continue
                n_rows = len(df)
                total_rows += n_rows
                # Per-code summary (stats only, not the rows themselves)
                close = df["close"] if "close" in df.columns else None
                volume = df["volume"] if "volume" in df.columns else None
                s = {"rows": n_rows, "status": "ok"}
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

            persisted_rows = 0
            if persist:
                if not workspace:
                    return err_actionable(
                        "persist=True requires 'workspace' (auto-injected by AgentLoop)",
                        expected="workspace root path, e.g. '/home/user/research'",
                        fix="persist=True is the default; if you only need to inspect "
                            "data, set persist=False instead",
                        tool="get_market_data",
                    )
                from ...db import save_ohlcv_to_db

                persisted_rows = save_ohlcv_to_db(
                    Path(workspace), data, strategy_name=strategy_name
                )

            return _ok({
                "summary": summary,
                "preview": preview,
                "persisted": persist,
                "strategy_name": strategy_name,
                "persisted_rows": persisted_rows,
                "meta": {
                    "codes": codes,
                    "start_date": start_date,
                    "end_date": end_date,
                    "interval": interval,
                    "source": effective_source,
                    "total_rows": total_rows,
                },
            })

        except NoAvailableSourceError as exc:
            return err_actionable(
                f"no available data source: {exc}",
                received=codes,
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
    """列出可用数据源及其状态。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 列出全部注册数据源: 可用性/适用市场/是否需要 API key。
    # 取数前先查可用源, 或排障时确认数据源状态。
    #
    # ## 参数
    # 无
    #
    # ## 示例
    # {}
    #
    # ## 边界
    # 只读工具; 不访问网络。
    #
    # ## 错误处理范式
    # 无输入参数, 极少失败; 失败均可安全重试。
    #
    # ## 相关工具
    # get_market_data: 用可用源取数
    # ─────────────────────────────────────────────
    """

    name = "list_data_sources"
    description = "列出已注册数据源及可用性/市场/API key 要求。"
    repeatable = True
    category = "行情"

    def execute(
        self,
        ctx: ToolContext,
    ) -> str:
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
    """按名称/代码搜索证券代码（A 股主）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 按名称或代码模糊搜索证券 (A 股主, 经 akshare spot 数据)。
    #
    # ## 参数
    # - query: 查询词 (必填, 名称或代码)
    # - market: 市场过滤 (默认 'a_share')
    # - limit: 最大结果数 (默认 10)
    #
    # ## 示例
    # {"query": "茅台"}
    #
    # ## 边界
    # 只读工具; 依赖 akshare 与网络; 无匹配返回空列表 (非错误)。
    #
    # ## 错误处理范式
    # - 缺 query → error + expected 示例
    # - akshare 未装 → fix 安装
    # - 网络失败 → error + fix 换查询词/检查网络
    #
    # ## 相关工具
    # get_market_data: 搜到的代码直接取数
    # ─────────────────────────────────────────────
    """

    name = "search_symbol"
    description = "按名称/代码模糊搜索证券代码 (A 股主, akshare)。"
    repeatable = True
    category = "行情"

    def execute(
        self,
        ctx: ToolContext,
        query: str,
        market: str = "a_share",
        limit: int = 10,
    ) -> str:
        query = query or ""
        market = market or "a_share"

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
    """手动导入 OHLCV 数据到 DuckDB（非推荐主流程）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 手动/外部 OHLCV 数据导入 DuckDB。主流程是
    # get_market_data(persist=True); 本工具仅用于粘贴外部数据/CSV。
    #
    # ## 参数
    # - data: {asset_code: [记录列表]} (必填)
    # - strategy_name: 数据分区名 (默认 'default')
    #
    # ## 示例
    # {"data": {"600519.SH": [{"trade_date": "2023-12-11", "close": 1544.5}]}}
    #
    # ## 边界
    # 写工具 (effects: db); 支持 LLM 错误包裹 (JSON 字符串/单键 dict) 容错。
    #
    # ## 错误处理范式
    # - 缺 data → error + expected 结构示例
    # - 数据形状错误 → error + fix 提示用 get_market_data
    # - 均可安全重试 (INSERT OR REPLACE 幂等)
    #
    # ## 相关工具
    # get_market_data: 推荐主流程
    # ─────────────────────────────────────────────
    """

    name = "import_data"
    description = "手动导入 OHLCV 数据到 DuckDB (非推荐主流程; 主流程 get_market_data)。"
    repeatable = True
    category = "行情"
    effects = frozenset({EFFECT_DB})

    def execute(
        self,
        ctx: ToolContext,
        data: dict[str, Any],
        strategy_name: str = "default",
    ) -> str:
        workspace = ctx.workspace
        if not workspace:
            return err_actionable(
                "missing workspace context",
                expected="workspace path (AgentLoop 注入)",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="import_data",
            )

        # Defensive read: framework coercion handles JSON-string dicts;
        # single-key wrapping ("data": {...}) is unwrapped here.
        raw_data = data
        unwrapped = try_unwrap_dict(raw_data) if isinstance(raw_data, dict) else None
        data = unwrapped if unwrapped is not None else raw_data

        if not data:
            return err_actionable(
                "missing or invalid 'data' (expect dict from get_market_data)",
                expected="non-empty dict, e.g. {'600519.SH': [{'trade_date': '2023-12-11', 'close': 1544.555, ...}, ...]}",
                fix=(
                    "call get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31', "
                    "persist=True) to fetch and persist into DuckDB in one step"
                ),
                tool="import_data",
            )

        strategy_name = strategy_name or "default"

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
                    "start_date='2023-01-01', end_date='2023-12-31', "
                    "persist=True) to fetch and persist into DuckDB in one step"
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
                received=str(data)[:200],
                expected="dict[asset_code, list[record]] from get_market_data",
                fix="verify data shape and workspace is writable",
                tool="import_data",
            )


# ── 5. register_data_tools ──────────────────────────────────────────


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (
        GetMarketDataTool,
        ListDataSourcesTool,
        SearchSymbolTool,
        ImportDataTool,
    ):
        registry.register(tool_cls())
