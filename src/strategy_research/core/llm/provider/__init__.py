"""Provider adapter registry.

Each provider encapsulates its own:
- Default endpoint, model, max_tokens
- Thinking/reasoning token extraction
- HTTP headers / payload customization
- Error code parsing and exception mapping
- User-friendly error messages

Adding a new provider:
    1. Create provider/new_provider.py with NewProviderAdapter class
    2. Register it in _REGISTRY below

No core files (parser.py, openai_client.py, loop.py) need modification.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter
from .deepseek import DeepSeekAdapter
from .fallback import FallbackAdapter
from .kimi import KimiAdapter
from .minimax import MiniMaxAdapter
from .openai import OpenAIAdapter
from .qwen import QwenAdapter

_REGISTRY: dict[str, type[ProviderAdapter]] = {
    "openai": OpenAIAdapter,
    "deepseek": DeepSeekAdapter,
    "minimax": MiniMaxAdapter,
    "qwen": QwenAdapter,
    "kimi": KimiAdapter,
}


def get_provider(name: str | None) -> ProviderAdapter:
    """Get provider adapter by name. Unknown providers get fallback.

    The 'auto' / None / empty name resolves to FallbackAdapter.
    """
    if not name or name == "auto":
        return FallbackAdapter()
    cls = _REGISTRY.get(name, FallbackAdapter)
    return cls()


def register_provider(name: str, cls: type[ProviderAdapter]) -> None:
    """Register a new provider adapter.

    Args:
        name: provider identifier
        cls: ProviderAdapter subclass
    """
    _REGISTRY[name] = cls


def get_provider_defaults(name: str | None) -> dict[str, Any]:
    """Get default config values (base_url, model, max_tokens) for a provider.

    Falls back to empty dict if the provider is unknown.
    """
    adapter = get_provider(name)
    return {
        "base_url": adapter.default_base_url,
        "model": adapter.default_model,
        "max_tokens": adapter.default_max_tokens,
    }


__all__ = [
    "ProviderAdapter",
    "OpenAIAdapter",
    "DeepSeekAdapter",
    "MiniMaxAdapter",
    "QwenAdapter",
    "KimiAdapter",
    "FallbackAdapter",
    "get_provider",
    "register_provider",
    "get_provider_defaults",
]