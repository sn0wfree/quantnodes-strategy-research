"""MCPServer — Model Context Protocol server (research-only).

Exposes strategy-research tools via MCP protocol for external AI tools.
All tools are research-only (no trading/order execution).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class MCPTool:
    """An MCP tool definition."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    def to_schema(self) -> dict[str, Any]:
        """Convert to MCP tool schema."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


class MCPServer:
    """MCP server exposing research-only tools.

    Usage:
        server = MCPServer()
        server.register_default_tools()
        # Serve via stdio
        server.serve_stdio()
        # Or get tool list
        tools = server.list_tools()
    """

    def __init__(self, name: str = "strategy-research") -> None:
        self.name = name
        self._tools: dict[str, MCPTool] = {}

    def register(self, tool: MCPTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools as MCP schemas."""
        return [t.to_schema() for t in self._tools.values()]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Tool '{name}' not found"}

        try:
            result = tool.handler(**arguments)
            return {"content": [{"type": "text", "text": str(result)}]}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def register_default_tools(self) -> None:
        """Register all default research-only tools."""
        self._register_skill_tools()
        self._register_goal_tools()
        self._register_hypothesis_tools()
        self._register_backtest_tools()
        self._register_factor_tools()
        self._register_memory_tools()
        self._register_session_tools()
        self._register_swarm_tools()
        self._register_web_tools()
        self._register_data_tools()
        self._register_swarm_execution_tools()

    def _register_skill_tools(self) -> None:
        def list_skills(**kwargs: Any) -> str:
            from ..skills import SkillRegistry
            skills_dir = Path(__file__).parent.parent.parent / "templates" / ".skills"
            registry = SkillRegistry()
            registry.load_directory(skills_dir)
            skills = registry.list_all()
            return json.dumps([{"name": s.name, "category": s.category, "description": s.description} for s in skills], ensure_ascii=False)

        def load_skill(name: str = "", **kwargs: Any) -> str:
            from ..skills import SkillRegistry
            skills_dir = Path(__file__).parent.parent.parent / "templates" / ".skills"
            registry = SkillRegistry()
            registry.load_directory(skills_dir)
            skill = registry.get(name)
            if skill is None:
                return f"Skill '{name}' not found"
            return skill.content

        self.register(MCPTool(
            name="list_skills",
            description="列出所有可用技能",
            parameters={"type": "object", "properties": {}},
            handler=list_skills,
        ))
        self.register(MCPTool(
            name="load_skill",
            description="按名称加载技能内容",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名称"}},
                "required": ["name"],
            },
            handler=load_skill,
        ))

    def _register_goal_tools(self) -> None:
        def start_research_goal(objective: str = "", session_id: str = "default", **kwargs: Any) -> str:
            # Phase B: 真接 GoalStore (替代旧 stub 返回假数据)
            try:
                from ..goal import GoalStore, RiskTier
                store = GoalStore()
                goal = store.replace_goal(
                    session_id=session_id,
                    objective=objective,
                    criteria=["backtest_profitable", "risk_within_limits"],
                    source="mcp",
                    risk_tier=RiskTier.RESEARCH_GENERAL,
                )
                return json.dumps({
                    "status": "ok",
                    "goal_id": goal.goal_id,
                    "session_id": session_id,
                    "objective": goal.objective,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def get_research_goal(session_id: str = "default", **kwargs: Any) -> str:
            try:
                from ..goal import GoalStore
                store = GoalStore()
                goal = store.get_current_goal(session_id)
                if goal is None:
                    return json.dumps({"status": "no_active_goal", "session_id": session_id}, ensure_ascii=False)
                return json.dumps({
                    "goal_id": goal.goal_id,
                    "session_id": session_id,
                    "objective": goal.objective,
                    "ui_summary": goal.ui_summary,
                    "status": goal.status.value,
                    "criteria": store.list_criteria(goal.goal_id),
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="start_research_goal",
            description="创建研究目标（写入 GoalStore SQLite）",
            parameters={
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "研究目标"},
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                },
                "required": ["objective"],
            },
            handler=start_research_goal,
        ))
        self.register(MCPTool(
            name="get_research_goal",
            description="获取当前会话的研究目标",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                },
            },
            handler=get_research_goal,
        ))

        def add_goal_evidence(
            session_id: str = "default",
            goal_id: str = "",
            text: str = "",
            criterion_id: str | None = None,
            claim_id: str | None = None,
            source_type: str | None = None,
            source_uri: str | None = None,
            confidence: str | None = None,
            caveat: str | None = None,
            **kwargs: Any,
        ) -> str:
            try:
                from ..goal import EvidenceInput, GoalStore
                store = GoalStore()
                if not goal_id:
                    return json.dumps({"status": "error", "error": "goal_id is required"}, ensure_ascii=False)
                if not text:
                    return json.dumps({"status": "error", "error": "text is required"}, ensure_ascii=False)
                evidence = EvidenceInput(
                    text=text,
                    criterion_id=criterion_id or None,
                    claim_id=claim_id or None,
                    source_provider="mcp",
                    source_type=source_type or None,
                    source_uri=source_uri or None,
                    confidence=confidence or None,
                    caveat=caveat or None,
                )
                record = store.append_evidence(
                    session_id=session_id,
                    goal_id=goal_id,
                    expected_goal_id=goal_id,
                    evidence=evidence,
                )
                return json.dumps({
                    "status": "ok",
                    "evidence_id": record.evidence_id,
                    "goal_id": record.goal_id,
                    "criterion_id": record.criterion_id,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def update_research_goal_status(
            session_id: str = "default",
            goal_id: str = "",
            status: str = "",
            recap: str | None = None,
            **kwargs: Any,
        ) -> str:
            try:
                from ..goal import GoalRecord, GoalStatus, GoalStore
                store = GoalStore()
                if not goal_id:
                    return json.dumps({"status": "error", "error": "goal_id is required"}, ensure_ascii=False)
                if not status:
                    return json.dumps({"status": "error", "error": "status is required"}, ensure_ascii=False)
                try:
                    goal_status = GoalStatus(status)
                except ValueError:
                    valid = [s.value for s in GoalStatus]
                    return json.dumps({
                        "status": "error",
                        "error": f"invalid status: {status!r}. Valid: {valid}",
                    }, ensure_ascii=False)
                record = store.update_status(
                    session_id=session_id,
                    goal_id=goal_id,
                    expected_goal_id=goal_id,
                    status=goal_status,
                    recap=recap or None,
                )
                return json.dumps({
                    "status": "ok",
                    "goal_id": record.goal_id,
                    "new_status": record.status.value,
                    "progress": record.progress_percent,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="add_goal_evidence",
            description="向研究目标追加证据（自动更新 criterion 状态）",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                    "goal_id": {"type": "string", "description": "目标 ID"},
                    "text": {"type": "string", "description": "证据内容"},
                    "criterion_id": {"type": "string", "description": "关联的 criterion ID（可选）"},
                    "claim_id": {"type": "string", "description": "关联的 claim ID（可选）"},
                    "source_type": {"type": "string", "description": "来源类型（如 backtest、data、analysis）"},
                    "source_uri": {"type": "string", "description": "来源 URI（如文件路径、URL）"},
                    "confidence": {"type": "string", "description": "置信度（high/medium/low）"},
                    "caveat": {"type": "string", "description": "注意事项或局限性"},
                },
                "required": ["goal_id", "text"],
            },
            handler=add_goal_evidence,
        ))
        self.register(MCPTool(
            name="update_research_goal_status",
            description="更新研究目标状态（active/complete/cancelled/paused 等）",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                    "goal_id": {"type": "string", "description": "目标 ID"},
                    "status": {"type": "string", "description": "新状态", "enum": [
                        "active", "paused", "waiting_user", "needs_refresh",
                        "insufficient_evidence", "blocked", "complete", "cancelled",
                    ]},
                    "recap": {"type": "string", "description": "完成/取消时的总结"},
                },
                "required": ["goal_id", "status"],
            },
            handler=update_research_goal_status,
        ))

    def _register_hypothesis_tools(self) -> None:
        def list_hypotheses(**kwargs: Any) -> str:
            from ..hypothesis import HypothesisRegistry
            registry = HypothesisRegistry()
            hyps = registry.list()
            return json.dumps([{"id": h.hypothesis_id, "title": h.title, "status": h.status} for h in hyps], ensure_ascii=False)

        self.register(MCPTool(
            name="list_hypotheses",
            description="列出所有假说",
            parameters={"type": "object", "properties": {}},
            handler=list_hypotheses,
        ))

    def _register_backtest_tools(self) -> None:
        def run_backtest(workspace: str = "", strategy: str = "", **kwargs: Any) -> str:
            # Phase B: 真接 core.backtest.run_backtest_script (替代旧 stub)
            if not workspace or not strategy:
                return json.dumps({
                    "status": "error",
                    "error": "workspace 和 strategy 都是必需参数",
                }, ensure_ascii=False)
            try:
                from ..backtest import run_backtest_script
                ws_path = Path(workspace).resolve()
                result = run_backtest_script(
                    workspace_path=ws_path,
                    strategy_name=strategy,
                    action="mcp",
                    description="MCP-triggered backtest",
                    timeout=300,
                )
                return json.dumps({
                    "status": "ok" if result.get("success") else "error",
                    "workspace": workspace,
                    "strategy": strategy,
                    "run": result.get("run", ""),
                    "metrics": result.get("metrics", {}),
                    "error": result.get("error"),
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def validate_run(run_dir: str = "", market: str = "a_share", **kwargs: Any) -> str:
            # Phase B: 真接 core.validation.runner (替代旧 stub)
            if not run_dir:
                return json.dumps({"status": "error", "error": "run_dir 是必需参数"}, ensure_ascii=False)
            try:
                from .validation import MarketType, run_validation
                from .validation.cli import _build_synthetic_trades, _load_nav_synthetic

                run_path = Path(run_dir).resolve()
                market_enum = MarketType(market)

                equity_curve = _load_nav_synthetic(run_path)
                trades = _build_synthetic_trades(run_path)
                if equity_curve is None or len(equity_curve) == 0:
                    return json.dumps({
                        "status": "error",
                        "error": f"无法从 {run_dir} 加载 NAV 时间序列",
                    }, ensure_ascii=False)

                config = {"validation": {"monte_carlo": True, "bootstrap": True}}
                results = run_validation(
                    config=config,
                    equity_curve=equity_curve,
                    trades=trades,
                    market=market_enum,
                )
                return json.dumps({
                    "status": "ok",
                    "run_dir": run_dir,
                    "validation": results,
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="run_backtest",
            description="执行回测（真接 core.backtest.run_backtest_script）",
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "工作区路径"},
                    "strategy": {"type": "string", "description": "策略名称"},
                },
                "required": ["workspace", "strategy"],
            },
            handler=run_backtest,
        ))
        self.register(MCPTool(
            name="validate_run",
            description="验证回测结果（Monte Carlo + Bootstrap + Walk-Forward）",
            parameters={
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "回测运行目录"},
                    "market": {"type": "string", "description": "市场类型", "default": "a_share"},
                },
                "required": ["run_dir"],
            },
            handler=validate_run,
        ))

    def _register_factor_tools(self) -> None:
        def compute_factor(expression: str = "", workspace: str = "", asset: str = "", **kwargs: Any) -> str:
            # Phase B: 真接 core.compute_factor + core.db.load_price_data
            if not workspace or not asset:
                return json.dumps({
                    "status": "error",
                    "error": "workspace 和 asset 都是必需参数",
                }, ensure_ascii=False)
            try:
                from ..compute_factor import compute_factor as _compute
                from ..db import load_price_data

                ws_path = Path(workspace).resolve()
                prices = load_price_data(ws_path, asset)
                if prices is None or len(prices) == 0:
                    return json.dumps({
                        "status": "error",
                        "error": f"无法从 {workspace} 加载 {asset} 的价格数据",
                    }, ensure_ascii=False)

                result = _compute(expression, prices, factor_name=asset)
                # 转成 list 以便 JSON 序列化
                return json.dumps({
                    "status": "ok",
                    "expression": expression,
                    "asset": asset,
                    "factor_name": result.name,
                    "n_total": len(result),
                    "n_non_null": int(result.notna().sum()),
                    "first_date": str(result.index[0]) if len(result) > 0 else None,
                    "last_date": str(result.index[-1]) if len(result) > 0 else None,
                    "preview": result.dropna().head(5).to_list(),
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="compute_factor",
            description="计算因子值（真接 core.compute_factor + DuckDB）",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "因子表达式 (DSL)"},
                    "workspace": {"type": "string", "description": "工作区路径"},
                    "asset": {"type": "string", "description": "标的代码"},
                },
                "required": ["expression", "workspace", "asset"],
            },
            handler=compute_factor,
        ))

    def _register_memory_tools(self) -> None:
        def search_memory(query: str = "", limit: int = 5, **kwargs: Any) -> str:
            # Phase B: 真接 PersistentMemory.find_relevant (替代旧 stub 空列表)
            try:
                from ..memory import PersistentMemory
                mem = PersistentMemory()
                entries = mem.find_relevant(query, max_results=limit)
                return json.dumps({
                    "query": query,
                    "n_results": len(entries),
                    "results": [
                        {
                            "title": e.title,
                            "type": e.memory_type,
                            "description": e.description,
                            "preview": e.body[:200],
                        }
                        for e in entries
                    ],
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def add_memory(title: str = "", content: str = "", memory_type: str = "project", **kwargs: Any) -> str:
            try:
                from ..memory import PersistentMemory
                mem = PersistentMemory()
                path = mem.add(title, content, memory_type=memory_type)
                return json.dumps({
                    "status": "ok",
                    "title": title,
                    "path": str(path),
                    "memory_type": memory_type,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="search_memory",
            description="搜索持久化记忆（keyword scoring + recency boost）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 5},
                },
                "required": ["query"],
            },
            handler=search_memory,
        ))
        self.register(MCPTool(
            name="add_memory",
            description="添加持久化记忆",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "标题"},
                    "content": {"type": "string", "description": "内容"},
                    "memory_type": {"type": "string", "description": "类型 (user/feedback/project/reference)", "default": "project"},
                },
                "required": ["title", "content"],
            },
            handler=add_memory,
        ))

    def _register_session_tools(self) -> None:
        def list_sessions(**kwargs: Any) -> str:
            from ..session import SessionDB
            db = SessionDB()
            sessions = db.list_sessions()
            return json.dumps([{"id": s.id, "workspace": s.workspace} for s in sessions], ensure_ascii=False)

        def search_messages(query: str = "", **kwargs: Any) -> str:
            from ..session import SessionDB
            db = SessionDB()
            results = db.search_messages(query)
            return json.dumps(results, ensure_ascii=False)

        self.register(MCPTool(
            name="list_sessions",
            description="列出所有会话",
            parameters={"type": "object", "properties": {}},
            handler=list_sessions,
        ))
        self.register(MCPTool(
            name="search_messages",
            description="搜索消息",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
            handler=search_messages,
        ))

    def _register_swarm_tools(self) -> None:
        def list_swarm_presets(**kwargs: Any) -> str:
            from ..swarm import list_presets
            presets_dir = Path(__file__).parent.parent / "swarm" / "presets"
            presets = list_presets(presets_dir)
            return json.dumps([{"name": p.name, "description": p.description, "agents": len(p.agents)} for p in presets], ensure_ascii=False)

        self.register(MCPTool(
            name="list_swarm_presets",
            description="列出所有 swarm preset",
            parameters={"type": "object", "properties": {}},
            handler=list_swarm_presets,
        ))

    def _register_web_tools(self) -> None:
        def web_search_tool(query: str = "", max_results: int = 10, **kwargs: Any) -> str:
            from ..web.search import web_search
            return web_search(query=query, max_results=max_results)

        def read_url_tool(url: str = "", max_chars: int = 10000, **kwargs: Any) -> str:
            from ..web.fetch import read_url
            return read_url(url=url, max_chars=max_chars)

        def read_document_tool(path: str = "", max_pages: int = 50, **kwargs: Any) -> str:
            from ..web.pdf import read_document
            return read_document(path=path, max_pages=max_pages)

        self.register(MCPTool(
            name="web_search",
            description="使用 DuckDuckGo 搜索互联网（无需 API key）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "最大结果数（默认 10）", "default": 10},
                },
                "required": ["query"],
            },
            handler=web_search_tool,
        ))
        self.register(MCPTool(
            name="read_url",
            description="抓取网页内容并转换为 Markdown",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的 URL"},
                    "max_chars": {"type": "integer", "description": "最大字符数（默认 10000）", "default": 10000},
                },
                "required": ["url"],
            },
            handler=read_url_tool,
        ))
        self.register(MCPTool(
            name="read_document",
            description="从 PDF 文件中提取文本内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "PDF 文件路径"},
                    "max_pages": {"type": "integer", "description": "最大页数（默认 50）", "default": 50},
                },
                "required": ["path"],
            },
            handler=read_document_tool,
        ))

    def _register_data_tools(self) -> None:
        def get_market_data(
            codes: list[str] | None = None,
            start_date: str = "",
            end_date: str = "",
            interval: str = "1D",
            source: str | None = None,
            max_rows: int = 500,
            **kwargs: Any,
        ) -> str:
            from ..data_source.base import validate_date_range
            from ..data_source.registry import (
                LOADER_REGISTRY,
                NoAvailableSourceError,
                resolve_loader,
            )
            from ..data_source.utils import detect_market

            if not codes:
                return json.dumps({"status": "error", "error": "codes is required"}, ensure_ascii=False)
            if not start_date or not end_date:
                return json.dumps({"status": "error", "error": "start_date and end_date are required"}, ensure_ascii=False)
            try:
                validate_date_range(start_date, end_date)
            except ValueError as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

            try:
                if source and source in LOADER_REGISTRY:
                    loader = LOADER_REGISTRY[source]()
                    if not loader.is_available():
                        return json.dumps({"status": "error", "error": f"source '{source}' is not available"}, ensure_ascii=False)
                    effective_source = source
                else:
                    market = detect_market(codes[0])
                    loader = resolve_loader(market)
                    effective_source = loader.name

                data = loader.fetch(codes, start_date, end_date, interval=interval)

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

                return json.dumps({
                    "status": "ok",
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
                }, ensure_ascii=False, default=str)

            except NoAvailableSourceError as exc:
                return json.dumps({"status": "error", "error": f"no available data source: {exc}"}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": f"fetch failed: {exc}"}, ensure_ascii=False)

        def list_data_sources(**kwargs: Any) -> str:
            from ..data_source.registry import LOADER_REGISTRY, _ensure_registered
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
            return json.dumps({
                "status": "ok",
                "n_sources": len(sources),
                "sources": sources,
            }, ensure_ascii=False)

        def search_symbol(
            query: str = "",
            market: str = "a_share",
            limit: int = 10,
            **kwargs: Any,
        ) -> str:
            if not query:
                return json.dumps({"status": "error", "error": "query is required"}, ensure_ascii=False)
            try:
                import akshare as ak
                df = ak.stock_zh_a_spot_em()
                if df is None or df.empty:
                    return json.dumps({"status": "ok", "results": [], "query": query}, ensure_ascii=False)
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
                    })
                return json.dumps({
                    "status": "ok",
                    "results": results,
                    "query": query,
                    "n_results": len(results),
                }, ensure_ascii=False)
            except ImportError:
                return json.dumps({"status": "error", "error": "akshare not installed"}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": f"search failed: {exc}"}, ensure_ascii=False)

        self.register(MCPTool(
            name="get_market_data",
            description="获取 OHLCV 市场行情数据（自动选择最佳数据源）",
            parameters={
                "type": "object",
                "properties": {
                    "codes": {"type": "array", "items": {"type": "string"}, "description": "资产代码列表"},
                    "start_date": {"type": "string", "description": "开始日期 (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "结束日期 (YYYY-MM-DD)"},
                    "interval": {"type": "string", "description": "K 线周期（默认 1D）", "default": "1D"},
                    "source": {"type": "string", "description": "指定数据源（可选）"},
                    "max_rows": {"type": "integer", "description": "每个代码最大行数（默认 500）", "default": 500},
                },
                "required": ["codes", "start_date", "end_date"],
            },
            handler=get_market_data,
        ))
        self.register(MCPTool(
            name="list_data_sources",
            description="列出所有可用数据源及其状态",
            parameters={"type": "object", "properties": {}},
            handler=list_data_sources,
        ))
        self.register(MCPTool(
            name="search_symbol",
            description="按名称或代码搜索股票/基金",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（名称或代码）"},
                    "market": {"type": "string", "description": "市场过滤（默认 a_share）", "default": "a_share"},
                    "limit": {"type": "integer", "description": "最大结果数（默认 10）", "default": 10},
                },
                "required": ["query"],
            },
            handler=search_symbol,
        ))

    def _register_swarm_execution_tools(self) -> None:
        from ..swarm.run_store import RunStore
        run_store = RunStore()

        def run_swarm(
            preset_name: str = "",
            workspace: str = "",
            task: str = "",
            max_workers: int = 4,
            timeout: int = 300,
            **kwargs: Any,
        ) -> str:
            if not preset_name:
                return json.dumps({"status": "error", "error": "preset_name is required"}, ensure_ascii=False)
            if not workspace:
                return json.dumps({"status": "error", "error": "workspace is required"}, ensure_ascii=False)
            if not task:
                task = f"执行 swarm preset: {preset_name}"

            from pathlib import Path
            from ..swarm import SwarmRuntime, load_preset

            presets_dir = Path(__file__).parent.parent / "swarm" / "presets"
            preset = None
            for ext in ("*.yaml", "*.yml"):
                for f in presets_dir.glob(ext):
                    p = load_preset(f)
                    if p and p.name == preset_name:
                        preset = p
                        break
                if preset:
                    break

            if preset is None:
                return json.dumps({
                    "status": "error",
                    "error": f"preset '{preset_name}' not found",
                }, ensure_ascii=False)

            try:
                runtime = SwarmRuntime(max_workers=max_workers)
                result = runtime.execute(preset, Path(workspace), task)

                # Serialize result
                agent_results = {}
                for aid, ar in result.agent_results.items():
                    agent_results[aid] = {
                        "status": ar.status.value if hasattr(ar.status, "value") else str(ar.status),
                        "elapsed_s": ar.elapsed_s,
                        "error": ar.error,
                    }

                result_dict = {
                    "run_id": result.run_id,
                    "preset_name": result.preset_name,
                    "success": result.success,
                    "elapsed_s": result.elapsed_s,
                    "agent_results": agent_results,
                    "final_output": result.final_output[:2000] if result.final_output else "",
                }
                run_store.save(result.run_id, result_dict)

                return json.dumps({
                    "status": "ok",
                    **result_dict,
                }, ensure_ascii=False, default=str)

            except Exception as exc:
                return json.dumps({
                    "status": "error",
                    "error": f"swarm execution failed: {exc}",
                }, ensure_ascii=False)

        def get_swarm_status(run_id: str = "", **kwargs: Any) -> str:
            if not run_id:
                return json.dumps({"status": "error", "error": "run_id is required"}, ensure_ascii=False)

            stored = run_store.get(run_id)
            if stored is not None:
                return json.dumps({
                    "status": "ok",
                    "run_status": "completed",
                    **stored,
                }, ensure_ascii=False, default=str)

            return json.dumps({
                "status": "ok",
                "run_status": "not_found",
                "run_id": run_id,
            }, ensure_ascii=False)

        self.register(MCPTool(
            name="run_swarm",
            description="执行 swarm preset（同步阻塞，直到完成或超时）",
            parameters={
                "type": "object",
                "properties": {
                    "preset_name": {"type": "string", "description": "Swarm preset 名称"},
                    "workspace": {"type": "string", "description": "工作区路径"},
                    "task": {"type": "string", "description": "任务描述"},
                    "max_workers": {"type": "integer", "description": "最大并行 worker 数（默认 4）", "default": 4},
                    "timeout": {"type": "integer", "description": "超时秒数（默认 300）", "default": 300},
                },
                "required": ["preset_name", "workspace"],
            },
            handler=run_swarm,
        ))
        self.register(MCPTool(
            name="get_swarm_status",
            description="查询 swarm 运行状态",
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "运行 ID"},
                },
                "required": ["run_id"],
            },
            handler=get_swarm_status,
        ))

    def serve_stdio(self) -> None:
        """Serve via stdin/stdout (blocking)."""
        import sys
        logger.info("MCP server '%s' starting on stdio", self.name)
        print(json.dumps({"server": self.name, "tools": self.list_tools()}))
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                method = request.get("method", "")
                params = request.get("params", {})
                req_id = request.get("id")

                if method == "tools/list":
                    response = {"tools": self.list_tools()}
                elif method == "tools/call":
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})
                    response = self.call_tool(tool_name, arguments)
                else:
                    response = {"error": f"Unknown method: {method}"}

                if req_id is not None:
                    print(json.dumps({"id": req_id, "result": response}))
            except json.JSONDecodeError:
                print(json.dumps({"error": "Invalid JSON"}))
