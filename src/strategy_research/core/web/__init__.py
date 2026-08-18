"""Web I/O module — URL fetching, web search, PDF extraction."""

from .fetch import read_url
from .minimax_search import (
    ENV_KEYS,
    has_minimax_credentials,
    minimax_search,
)
from .search import web_search

__all__ = [
    "webfetch",
    "websearch",
    "minimax_search",
    "has_minimax_credentials",
    "ENV_KEYS",
]