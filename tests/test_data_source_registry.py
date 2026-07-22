"""Tests for data_source/registry.py — loader registration + fallback resolution."""

from __future__ import annotations

import pytest

from strategy_research.core.data_source import registry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset _registered flag to False so _ensure_registered runs fresh per test."""
    registry._registered = False
    yield
    registry._registered = False


# ============================================================
# Registration
# ============================================================


class TestRegistration:
    def test_register_adds_to_registry(self):
        class FakeLoader:
            name = "fake"

        registry.register(FakeLoader)
        assert "fake" in registry.LOADER_REGISTRY
        assert registry.LOADER_REGISTRY["fake"] is FakeLoader

    def test_register_returns_class(self):
        class FakeLoader:
            name = "fake2"

        result = registry.register(FakeLoader)
        assert result is FakeLoader

    def test_get_loader_returns_registered(self):
        class FakeLoader:
            name = "fake3"

        registry.register(FakeLoader)
        result = registry.get_loader("fake3")
        assert result is FakeLoader

    def test_get_loader_unknown_returns_none(self):
        result = registry.get_loader("nonexistent_loader_xyz")
        assert result is None

    def test_list_loaders_includes_registered(self):
        class FakeLoader:
            name = "fake4"

        registry.register(FakeLoader)
        loaders = registry.list_loaders()
        assert "fake4" in loaders


# ============================================================
# FALLBACK_CHAINS
# ============================================================


class TestFallbackChains:
    def test_a_share_chain_exists(self):
        assert "a_share" in registry.FALLBACK_CHAINS
        assert len(registry.FALLBACK_CHAINS["a_share"]) > 0

    def test_hk_chain_exists(self):
        assert "hk" in registry.FALLBACK_CHAINS

    def test_us_chain_exists(self):
        assert "us" in registry.FALLBACK_CHAINS

    def test_crypto_chain_exists(self):
        assert "crypto" in registry.FALLBACK_CHAINS

    def test_macro_chain_exists(self):
        assert "macro" in registry.FALLBACK_CHAINS

    def test_chains_include_local(self):
        # All chains should have local as a fallback
        for market, chain in registry.FALLBACK_CHAINS.items():
            assert "local" in chain, f"Chain for {market} missing local fallback"

    def test_no_network_fallback_is_local(self):
        assert "local" in registry._NO_NETWORK_FALLBACK


# ============================================================
# NoAvailableSourceError
# ============================================================


class TestNoAvailableSourceError:
    def test_exception_can_be_raised(self):
        with pytest.raises(registry.NoAvailableSourceError):
            raise registry.NoAvailableSourceError("test message")

    def test_exception_message(self):
        err = registry.NoAvailableSourceError("custom message")
        assert str(err) == "custom message"

    def test_is_exception_subclass(self):
        assert issubclass(registry.NoAvailableSourceError, Exception)


# ============================================================
# resolve_loader_with_fallback
# ============================================================


class TestResolveLoaderWithFallback:
    def test_unknown_source_raises(self):
        with pytest.raises(registry.NoAvailableSourceError, match="未知数据源"):
            registry.resolve_loader_with_fallback("totally_made_up_source_123")

    def test_local_loader_no_network_fallback(self):
        # Local loader is registered and always available
        # If unavailable, should raise (no fallback to network)
        result = registry.resolve_loader_with_fallback("local")
        # local is always available, returns the class
        assert result is not None


# ============================================================
# get_loader_or_fallback
# ============================================================


class TestGetLoaderOrFallback:
    def test_unknown_source_raises(self):
        with pytest.raises(registry.NoAvailableSourceError):
            registry.get_loader_or_fallback("nonexistent_loader_xyz_abc")


# ============================================================
# resolve_loader
# ============================================================


class TestResolveLoader:
    def test_unknown_market_raises(self):
        # Unknown market with no chain → NoAvailableSourceError
        with pytest.raises(registry.NoAvailableSourceError):
            registry.resolve_loader("totally_made_up_market_xyz")


# ============================================================
# _ensure_registered
# ============================================================


class TestEnsureRegistered:
    def test_registers_at_least_local(self):
        registry._registered = False
        registry._ensure_registered()
        assert registry._registered is True
        # local loader should be registered
        assert "local" in registry.LOADER_REGISTRY

    def test_idempotent(self):
        registry._ensure_registered()
        first_count = len(registry.LOADER_REGISTRY)
        registry._ensure_registered()
        # Should not re-register
        assert len(registry.LOADER_REGISTRY) == first_count