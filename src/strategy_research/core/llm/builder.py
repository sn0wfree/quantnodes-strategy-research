"""LLMConfig builder — fluent composition of LLMConfig (Phase 2.4).

``LLMConfig.load`` has accumulated 4 distinct sources (code defaults,
bridge JSON, env vars, CLI overrides) with translation steps between
each layer. The Builder pattern makes the layering explicit and lets
callers inject custom intermediate steps.

The ``LLMConfigBuilder`` wraps the shared :class:`ConfigBuilder` from
``core.config_loader`` plus LLM-specific post-hooks for:
- Provider→base_url/model/max_tokens fallback (PROVIDER_DEFAULTS)
- api_key resolution (OPENAI_API_KEY > LLM_API_KEY > bridge)
- max_tokens default fallback

Typical usage::

    cfg = (LLMConfigBuilder()
           .with_code_defaults()
           .with_bridge_yaml(path)
           .with_env_overrides(env)
           .with_cli_overrides(args)
           .build())

is functionally equivalent to ``LLMConfig.load()`` for the default case.
Builders can be subclassed to customise individual layers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config_loader import ConfigBuilder, load_layered_config
from .config import (
    ENV_API_KEY,
    PROVIDER_DEFAULTS,
    QUANTNODES_LLM_JSON,
    _DEFAULT_MAX_TOKENS,
    _load_bridge_dict,
    _resolve_bridge_path,
    load_api_key_from_env,
)

logger = logging.getLogger(__name__)


class LLMConfigBuilder:
    """Fluent builder for LLMConfig composition.

    Mirrors the layering in :meth:`LLMConfig.load` but makes each layer
    explicit. Useful for:
    - Custom callers that don't want dotenv/CLI auto-load
    - Test fixtures that need deterministic config composition
    - New integration points (e.g. MCP server, hooks) that need a subset
      of the layer cascade

    All ``with_*`` methods return ``self`` for chaining.
    """

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(env if env is not None else os.environ)
        self._builder = ConfigBuilder(defaults={})
        self._raw_cli: Mapping[str, Any] | None = None
        self._cli_overrides_dict: dict[str, Any] | None = None

    # ── Layer composition (each returns self) ──────────────────

    def with_code_defaults(self, defaults: Mapping[str, Any] | None = None) -> "LLMConfigBuilder":
        """Inject the dataclass-level defaults as the lowest-priority layer.

        ``defaults`` defaults to a fresh ``LLMConfig()`` instance's
        ``dataclasses.asdict`` output, but tests may inject a custom dict.
        """
        if defaults is None:
            import dataclasses as _dc
            from .config import LLMConfig
            defaults = {f.name: getattr(LLMConfig(), f.name)
                        for f in _dc.fields(LLMConfig)}
        self._builder = ConfigBuilder(defaults=defaults)
        return self

    def with_bridge_yaml(self, path: Path | None = None) -> "LLMConfigBuilder":
        """Read the bridge JSON file (~/.quantnodes/llm.json) into the cascade."""
        eff_path = path if path is not None else _resolve_bridge_path(self._env)
        bridge_data = _load_bridge_dict(eff_path)
        if bridge_data:
            self._builder = self._builder.with_yaml_data("bridge", bridge_data)
        return self

    def with_env_overrides(
        self, env: Mapping[str, str] | None = None,
    ) -> "LLMConfigBuilder":
        """Apply the OPENAI_* env-var layer (base_url, model)."""
        eff_env = env if env is not None else self._env
        overrides: dict[str, Any] = {}
        if eff_env.get(ENV_BASE_URL := "OPENAI_BASE_URL"):
            overrides["base_url"] = eff_env["OPENAI_BASE_URL"]
        if eff_env.get(ENV_MODEL := "OPENAI_MODEL"):
            overrides["model"] = eff_env["OPENAI_MODEL"]
        self._builder = self._builder.with_yaml_data("env", overrides)
        return self

    def with_cli_overrides(self, cli: Mapping[str, Any] | None) -> "LLMConfigBuilder":
        """Apply the CLI override layer (truthy ``llm_*`` keys)."""
        self._raw_cli = cli
        if cli:
            overrides: dict[str, Any] = {}
            for key, value in cli.items():
                if not key.startswith("llm_"):
                    continue
                if value is None:
                    continue
                field = key[len("llm_"):]
                overrides[field] = value
            self._cli_overrides_dict = overrides
            self._builder = self._builder.with_cli_overrides(overrides)
        return self

    def with_post_hook(
        self, hook,
    ) -> "LLMConfigBuilder":
        """Register a custom post-merge transformation (escape hatch)."""
        self._builder = self._builder.with_post_hook(hook)
        return self

    # ── Build ─────────────────────────────────────────────────

    def build(self) -> dict[str, Any]:
        """Apply all layers and standard LLM-specific hooks.

        Order of operations (after the basic merge):
        1. Provider→base_url/model/max_tokens fallback (PROVIDER_DEFAULTS)
        2. api_key resolution (env vars preferred over bridge value)
        3. max_tokens default fallback

        Returns:
            A plain ``dict`` matching LLMConfig field names. The caller
            is responsible for constructing the ``LLMConfig`` instance
            (typically ``LLMConfig(**result)``).
        """
        from .config import LLMConfig

        # Standard post-hooks applied in registration order.
        self._builder = (
            self._builder
            .with_post_hook(_provider_fallback_hook)
            .with_post_hook(_api_key_hook(self._env))
            .with_post_hook(_max_tokens_default_hook)
        )
        merged = self._builder.build()

        # Convert to LLMConfig so the post-hook output is validated.
        try:
            cfg = LLMConfig(**{k: v for k, v in merged.items()
                                if k in {f.name for f in
                                         __import__("dataclasses").fields(LLMConfig)}})
            # Preserve compact_config separately if it was a dataclass
            if "compact_config" in merged and merged["compact_config"] is not None:
                cfg = cfg.with_config(compact_config=merged["compact_config"])
            return _config_to_dict(cfg)
        except TypeError as exc:
            logger.warning("LLMConfigBuilder: construction failed (%s); "
                           "returning raw dict", exc)
            return merged


# ── Standard post-hooks ─────────────────────────────────────────────


def _provider_fallback_hook(merged: dict[str, Any]) -> dict[str, Any] | None:
    """Apply provider→base_url/model/max_tokens fallback when missing."""
    provider = merged.get("provider")
    if not provider:
        return None
    defaults = PROVIDER_DEFAULTS.get(provider)
    if not defaults:
        return None
    if not merged.get("base_url"):
        merged["base_url"] = defaults["base_url"]
    if not merged.get("model") and defaults.get("model"):
        merged["model"] = defaults["model"]
    if merged.get("max_tokens") is None and defaults.get("max_tokens"):
        merged["max_tokens"] = defaults["max_tokens"]
    return merged


def _api_key_hook(env: Mapping[str, str]):
    """Build a post-hook that resolves ``api_key`` from env vars."""
    def hook(merged: dict[str, Any]) -> dict[str, Any] | None:
        if merged.get("api_key"):
            return None  # already set
        merged["api_key"] = load_api_key_from_env(env)
        return merged
    return hook


def _max_tokens_default_hook(merged: dict[str, Any]) -> dict[str, Any] | None:
    """Apply the global max_tokens default fallback."""
    if merged.get("max_tokens") is None:
        merged["max_tokens"] = _DEFAULT_MAX_TOKENS
    return merged


def _config_to_dict(cfg) -> dict[str, Any]:
    """Convert an LLMConfig to a plain dict (for builder output)."""
    import dataclasses as _dc
    out = {}
    for f in _dc.fields(cfg):
        v = getattr(cfg, f.name)
        if v is None:
            continue
        out[f.name] = v
    return out


__all__ = [
    "LLMConfigBuilder",
    "QUANTNODES_LLM_JSON",
]