"""Agent tools for Web I/O: web_search, read_url, read_document."""

from __future__ import annotations

import json
import logging
from pathlib import Path
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


# ── 1. WebSearchTool ─────────────────────────────────────────────


class WebSearchTool(BaseTool):
    """Search the web using DuckDuckGo."""

    name = "web_search"
    description = (
        "Search the web using DuckDuckGo. Returns top results with title, URL, "
        "and snippet. No API key required."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Max results (default 10)."},
        },
        "required": ["query"],
    }
    repeatable = True

    @classmethod
    def check_available(cls) -> bool:
        try:
            import duckduckgo_search  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(self, **kwargs: Any) -> str:
        from ...core.web.search import web_search
        query = kwargs.get("query", "")
        max_results = int(kwargs.get("max_results", 10))
        return web_search(query=query, max_results=max_results)


# ── 2. ReadUrlTool ───────────────────────────────────────────────


class ReadUrlTool(BaseTool):
    """Fetch a URL and convert HTML to Markdown."""

    name = "read_url"
    description = (
        "Fetch a web page URL and return its content as Markdown. "
        "Useful for reading documentation, articles, and research papers."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
            "max_chars": {"type": "integer", "description": "Max characters (default 10000)."},
        },
        "required": ["url"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        from ...core.web.fetch import read_url
        url = kwargs.get("url", "")
        max_chars = int(kwargs.get("max_chars", 10_000))
        return read_url(url=url, max_chars=max_chars)


# ── 3. ReadDocumentTool ──────────────────────────────────────────


class ReadDocumentTool(BaseTool):
    """Extract text from a PDF document."""

    name = "read_document"
    description = (
        "Extract text content from a PDF file. Returns extracted text "
        "with page markers. Requires PyMuPDF (optional dependency)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to PDF file."},
            "max_pages": {"type": "integer", "description": "Max pages (default 50)."},
        },
        "required": ["path"],
    }
    repeatable = True

    @classmethod
    def check_available(cls) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(self, **kwargs: Any) -> str:
        from ...core.web.pdf import read_document
        path = kwargs.get("path", "")
        max_pages = int(kwargs.get("max_pages", 50))
        return read_document(path=path, max_pages=max_pages)


def register_web_tools(registry: ToolRegistry) -> None:
    """Register all web tools into a ToolRegistry."""
    for tool_cls in (WebSearchTool, ReadUrlTool, ReadDocumentTool):
        if tool_cls.check_available():
            registry.register(tool_cls())
