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

    from strategy_research.api.app import create_app, configure_from_env
    from strategy_research.cli.llm_config_check import check_llm_config
    from strategy_research.webui.routes import router as webui_router

    # Read env config (CORS_ORIGINS, JWT_SECRET, STATIC_DIR)
    env_config = configure_from_env()

    workspace = Path(args.workspace)
    static_dir = getattr(args, "static_dir", None) or env_config.get("static_dir")

    app = create_app(
        workspace_path=workspace if workspace.exists() else None,
        goal_db_path=getattr(args, "goal_db", None),
        hypotheses_path=getattr(args, "hypotheses_path", None),
        static_dir=static_dir,
        cors_origins=env_config.get("cors_origins"),
    )

    # Mount webui routes
    app.include_router(webui_router, tags=["webui"])

    # ── Startup banner ──────────────────────────────────────────────
    print(f"🌐 Strategy Research Web UI starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")
    if static_dir:
        print(f"   Static files: {static_dir}")
    print(f"   Docs:         http://{args.host}:{args.port}/docs")

    # ── LLM 配置状态检测 ──────────────────────────────────────────
    try:
        llm_status = check_llm_config()
        if llm_status["configured"]:
            print(
                f"✓ LLM 配置：{llm_status['provider']} / {llm_status['model']} "
                f"(api_key={llm_status['api_key_source']})"
            )
        else:
            print("⚠ LLM 配置未完成（聊天 API 会返回 503）：")
            print("   - 缺少 ~/.quantnodes/llm.json 或 .env")
            print("   - 运行：quantnodes-research init")
            print("   - 或设置：export OPENAI_API_KEY='sk-...'")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ LLM 配置检测失败: {exc}")

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
