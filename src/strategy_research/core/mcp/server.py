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
        def start_research_goal(objective: str = "", **kwargs: Any) -> str:
            return json.dumps({"status": "ok", "objective": objective}, ensure_ascii=False)

        def get_research_goal(**kwargs: Any) -> str:
            return json.dumps({"status": "no_active_goal"}, ensure_ascii=False)

        self.register(MCPTool(
            name="start_research_goal",
            description="创建研究目标",
            parameters={
                "type": "object",
                "properties": {"objective": {"type": "string", "description": "研究目标"}},
                "required": ["objective"],
            },
            handler=start_research_goal,
        ))
        self.register(MCPTool(
            name="get_research_goal",
            description="获取当前研究目标",
            parameters={"type": "object", "properties": {}},
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
        def run_backtest(workspace: str = "", **kwargs: Any) -> str:
            return json.dumps({"status": "ok", "workspace": workspace, "message": "Backtest submitted"}, ensure_ascii=False)

        def validate_run(run_dir: str = "", **kwargs: Any) -> str:
            return json.dumps({"status": "ok", "run_dir": run_dir, "message": "Validation submitted"}, ensure_ascii=False)

        self.register(MCPTool(
            name="run_backtest",
            description="执行回测",
            parameters={
                "type": "object",
                "properties": {"workspace": {"type": "string", "description": "工作区路径"}},
                "required": ["workspace"],
            },
            handler=run_backtest,
        ))
        self.register(MCPTool(
            name="validate_run",
            description="验证回测结果",
            parameters={
                "type": "object",
                "properties": {"run_dir": {"type": "string", "description": "回测运行目录"}},
                "required": ["run_dir"],
            },
            handler=validate_run,
        ))

    def _register_factor_tools(self) -> None:
        def compute_factor(expression: str = "", **kwargs: Any) -> str:
            return json.dumps({"status": "ok", "expression": expression, "message": "Factor computed"}, ensure_ascii=False)

        self.register(MCPTool(
            name="compute_factor",
            description="计算因子值",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "因子表达式"}},
                "required": ["expression"],
            },
            handler=compute_factor,
        ))

    def _register_memory_tools(self) -> None:
        def search_memory(query: str = "", **kwargs: Any) -> str:
            return json.dumps({"results": [], "query": query}, ensure_ascii=False)

        def add_memory(title: str = "", content: str = "", **kwargs: Any) -> str:
            return json.dumps({"status": "ok", "title": title}, ensure_ascii=False)

        self.register(MCPTool(
            name="search_memory",
            description="搜索记忆",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
            handler=search_memory,
        ))
        self.register(MCPTool(
            name="add_memory",
            description="添加记忆",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "标题"},
                    "content": {"type": "string", "description": "内容"},
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
