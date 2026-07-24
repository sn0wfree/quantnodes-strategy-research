"""DuckDuckGo web search with exponential backoff."""

from __future__ import annotations

import json
import logging
from typing import Any

from ._rate_limit import ExponentialBackoff

logger = logging.getLogger(__name__)

# 模块级单例限速器
_backoff = ExponentialBackoff(base=1.0, max_delay=30.0, factor=2.0)


def web_search(
    query: str,
    max_results: int = 10,
) -> str:
    """Search the web using DuckDuckGo.

    Args:
        query: Search query string.
        max_results: Maximum number of results (default 10).

    Returns:
        JSON string with search results.
    """
    if not query or not query.strip():
        return json.dumps({
            "status": "error",
            "error": "query is required",
        }, ensure_ascii=False)

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return json.dumps({
            "status": "error",
            "error": "duckduckgo-search package not installed. Install with: pip install duckduckgo-search",
        }, ensure_ascii=False)

    try:
        _backoff.wait()
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        _backoff.reset()

        return json.dumps({
            "status": "ok",
            "query": query,
            "max_results": max_results,
            "n_results": len(results),
            "results": [
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", ""),
                }
                for r in results
            ],
        }, ensure_ascii=False)

    except Exception as exc:
        logger.warning("web_search failed for %r: %s", query, exc)
        return json.dumps({
            "status": "error",
            "error": f"search failed: {exc}",
            "query": query,
        }, ensure_ascii=False)
