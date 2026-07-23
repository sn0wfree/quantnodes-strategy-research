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
                    "status": "ok",
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
                from .validation.cli import _load_nav_synthetic, _build_synthetic_trades

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
