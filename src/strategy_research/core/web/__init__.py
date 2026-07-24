"""Web I/O module — URL fetching, web search, PDF extraction."""

from .fetch import read_url
from .search import web_search

__all__ = ["read_url", "web_search"]
