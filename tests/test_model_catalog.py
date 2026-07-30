"""Tests for the ModelCatalog 3-layer fallback system.

Layers:
  L1 Bundled  — package data in core/llm/data/providers/<id>/<model>.toml
  L2 Cached   — disk cache ~/.quantnodes/model_catalog.json
  L3 Default  — _default_fallback.json for unknown providers/models
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from strategy_research.core.llm.model_catalog import (
    MODELS_DEV_ID,
    ModelCatalog,
    ModelInfo,
    models_dev_id,
    reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Redirect disk cache to a tmp dir for each test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Reset module-level singleton so a fresh catalog picks up the
    # new home env var.
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


# ── 1. Bundled data lookup ───────────────────────────────────────────


def test_get_info_returns_bundled_for_known_minimax():
    info = ModelCatalog().get_info("minimax", "minimax-M3")
    assert info.source == "bundled"
    assert info.context_tokens == 1_000_000
    assert info.max_output_tokens == 128_000
    assert info.supports_vision is True
    assert info.supports_tools is True
    assert info.supports_reasoning is True
    assert info.provider == "minimax"
    assert info.model == "minimax-M3"
    assert info.models_dev_id == "minimax-cn-coding-plan"


def test_get_info_finds_qwen_via_alibaba_alias():
    info = ModelCatalog().get_info("qwen", "qwen-plus")
    assert info.source == "bundled"
    assert info.models_dev_id == "alibaba"
    assert info.context_tokens > 0


def test_get_info_finds_kimi_via_moonshotai_alias():
    info = ModelCatalog().get_info("kimi", "kimi-k2.5")
    assert info.source == "bundled"
    assert info.models_dev_id == "moonshotai"


# ── 2. Fallback path ────────────────────────────────────────────────


def test_get_info_uses_default_fallback_for_unknown_provider():
    info = ModelCatalog().get_info("weird-llm", "X-1")
    assert info.source == "fallback"
    assert info.context_tokens == 8192
    assert info.max_output_tokens == 4096
    assert info.supports_vision is False
    assert info.supports_tools is True
    assert info.provider == "weird-llm"
    assert info.model == "X-1"


# ── 3. Path mapping ─────────────────────────────────────────────────


def test_models_dev_id_known_providers():
    assert models_dev_id("minimax") == "minimax-cn-coding-plan"
    assert models_dev_id("qwen") == "alibaba"
    assert models_dev_id("kimi") == "moonshotai"
    assert models_dev_id("openai") == "openai"
    assert models_dev_id("deepseek") == "deepseek"


def test_models_dev_id_unknown_passes_through_lower():
    assert models_dev_id("Custom-Name") == "custom-name"


def test_models_dev_id_mapping_complete():
    """The MODELS_DEV_ID dict has at least the 5 core providers."""
    for name in ("minimax", "openai", "deepseek", "qwen", "kimi"):
        assert name in MODELS_DEV_ID


# ── 4. Disk cache ───────────────────────────────────────────────────


def test_disk_cache_written_after_refresh(tmp_path, monkeypatch):
    """refresh_async writes ~/.quantnodes/model_catalog.json on success."""
    cache_path = Path(os.environ["HOME"]) / ".quantnodes" / "model_catalog.json"

    async def run():
        catalog = ModelCatalog()
        return await catalog.refresh_async("openai", "gpt-4o-mini")

    info = asyncio.run(run())

    assert info.source == "fetched"
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text())
    assert "openai/gpt-4o-mini" in payload
    assert payload["openai/gpt-4o-mini"]["context_tokens"] == 128_000
    # And the in-memory cached entry uses the field for downstream consumers
    assert info.context_tokens == 128_000


def test_cached_entry_returned_after_refresh(tmp_path, monkeypatch):
    """After refresh, get_info returns source='cached' (disk cache)."""

    async def setup():
        catalog = ModelCatalog()
        await catalog.refresh_async("openai", "gpt-4o-mini")

    asyncio.run(setup())

    info = ModelCatalog().get_info("openai", "gpt-4o-mini")
    assert info.source == "cached"
    assert info.context_tokens == 128_000


# ── 5. Failure path ─────────────────────────────────────────────────


def test_refresh_failure_falls_back_to_bundled(tmp_path, monkeypatch):
    """When all base URLs fail, refresh returns the bundled entry."""

    class FailFactory:
        def __call__(self):
            import httpx

            class FailClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def get(self, url, timeout=None):
                    import httpx

                    raise httpx.ConnectError("network unreachable")

            return FailClient()

    catalog = ModelCatalog(client_factory=FailFactory())

    async def run():
        return await catalog.refresh_async("openai", "gpt-4o-mini")

    info = asyncio.run(run())
    # Falls back to bundled (no disk cache yet)
    assert info.source == "bundled"
    assert info.context_tokens == 128_000


def test_get_info_returns_cached_when_disk_cache_exists_but_no_bundled():
    """Unknown model with disk cache: returns cached (even if stale)."""
    # Write a fake cache entry
    cache_path = Path(os.environ["HOME"]) / ".quantnodes" / "model_catalog.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "custom-llm/custom-model": {
            "provider": "custom-llm",
            "model": "custom-model",
            "models_dev_id": "custom-llm",
            "context_tokens": 32000,
            "max_output_tokens": 4096,
            "supports_vision": False,
            "supports_audio": False,
            "supports_pdf": False,
            "supports_tools": True,
            "supports_reasoning": False,
            "supports_structured_output": False,
            "cost_input": 0.1,
            "cost_output": 0.2,
            "cost_cache_read": None,
            "cost_cache_write": None,
            "description": "custom",
            "release_date": "2026-01-01",
            "source": "fetched",
            "fetched_at": "2026-07-30T00:00:00+00:00",
        }
    }))

    catalog = ModelCatalog()
    info = catalog.get_info("custom-llm", "custom-model")
    assert info.context_tokens == 32000
    # Either 'cached' (fresh) or 'fallback' (stale). Cached since just-written.
    assert info.source in ("cached", "fallback")
