"""Extracted from cli.py — server and tool commands.

Contains:
- cmd_webui_serve
- cmd_api_serve
- cmd_mcp_serve
- cmd_mcp_list_tools
"""

from __future__ import annotations

from pathlib import Path


def cmd_webui_serve(args) -> int:
    """启动 Web UI 服务器。"""
    import uvicorn

    from strategy_research.api.app import create_app
    from strategy_research.webui.routes import router as webui_router

    workspace = Path(args.workspace)
    app = create_app(
        workspace_path=workspace if workspace.exists() else None,
        goal_db_path=getattr(args, "goal_db", None),
        hypotheses_path=getattr(args, "hypotheses_path", None),
    )

    # Mount webui routes
    app.include_router(webui_router, tags=["webui"])

    print(f"🌐 Strategy Research Web UI starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=getattr(args, "reload", False),
    )
    return 0


def cmd_api_serve(args) -> int:
    """启动 HTTP API 服务器。"""
    import uvicorn

    from strategy_research.api.app import create_app

    workspace = Path(args.workspace)
    app = create_app(
        workspace_path=workspace if workspace.exists() else None,
        goal_db_path=getattr(args, "goal_db", None),
        hypotheses_path=getattr(args, "hypotheses_path", None),
    )

    print(f"🚀 Strategy Research API starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")
    print(f"   Docs:      http://{args.host}:{args.port}/docs")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=getattr(args, "reload", False),
    )
    return 0


def cmd_mcp_serve(args) -> int:
    """启动 MCP 服务器。"""
    from strategy_research.core.mcp import MCPServer

    server = MCPServer()
    server.register_default_tools()

    if args.transport == "stdio":
        server.serve_stdio()
    else:
        print(f"MCP SSE server not yet implemented (port {args.port})")
        return 1

    return 0


def cmd_mcp_list_tools(args) -> int:
    """列出所有 MCP 工具。"""
    from strategy_research.core.mcp import MCPServer

    server = MCPServer()
    server.register_default_tools()

    tools = server.list_tools()
    print(f"=== MCP Tools (共 {len(tools)} 个) ===")
    for t in tools:
        params = t.get("inputSchema", {}).get("properties", {})
        param_str = ", ".join(params.keys()) if params else "no params"
        print(f"  {t['name']:30s}  {param_str:30s}  {t['description'][:40]}")

    return 0
