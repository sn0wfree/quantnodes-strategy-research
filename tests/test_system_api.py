"""Tests for the /api/system/info model_info field and refresh endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.api.app import create_app

    app = create_app(workspace_path=None)
    return TestClient(app)


def test_info_includes_model_info_when_llm_configured(client):
    """When LLM is configured, system/info includes model_info."""
    fake_llm = {
        "configured": True,
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key_source": "env",
    }
    with patch(
        "strategy_research.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ):
        resp = client.get("/api/system/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["provider"] == "minimax"
    assert body["model_info"] is not None
    info = body["model_info"]
    assert info["provider"] == "minimax"
    assert info["context_tokens"] > 0
    assert "source" in info


def test_info_model_info_none_when_unconfigured(client):
    """When LLM is not configured, model_info is null."""
    fake_llm = {
        "configured": False,
        "provider": "unknown",
        "model": "unknown",
        "api_key_source": "unknown",
    }
    with patch(
        "strategy_research.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ):
        resp = client.get("/api/system/info")
    assert resp.status_code == 200
    assert resp.json()["model_info"] is None


def test_refresh_endpoint_returns_model_info(client):
    """POST /api/system/model-info/refresh returns ModelInfo."""
    fake_llm = {
        "configured": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key_source": "env",
    }
    with patch(
        "strategy_research.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ):
        resp = client.post(
            "/api/system/model-info/refresh",
            json={"provider": "openai", "model": "gpt-4o-mini"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini"
    assert body["context_tokens"] > 0


def test_refresh_endpoint_uses_current_config_when_no_body(client):
    """POST /api/system/model-info/refresh with empty body uses current LLM."""
    fake_llm = {
        "configured": True,
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key_source": "env",
    }
    with patch(
        "strategy_research.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ):
        resp = client.post("/api/system/model-info/refresh", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "minimax"


def test_refresh_endpoint_400_when_no_llm(client):
    """POST /api/system/model-info/refresh without LLM config returns 400."""
    fake_llm = {
        "configured": False,
        "provider": "unknown",
        "model": "unknown",
        "api_key_source": "unknown",
    }
    with patch(
        "strategy_research.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ):
        resp = client.post("/api/system/model-info/refresh", json={})
    assert resp.status_code == 400
