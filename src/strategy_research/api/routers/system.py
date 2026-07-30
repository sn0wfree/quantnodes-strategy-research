"""System info API — workspace status, LLM config, user count."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_QUANTNODES_DIR = Path.home() / ".quantnodes"
_LLM_JSON_PATH = _QUANTNODES_DIR / "llm.json"
_DOTENV_PATH = _QUANTNODES_DIR / ".env"


class LLMConfigUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


def _read_dotenv() -> dict[str, str]:
    """Read .env file into a dict."""
    result = {}
    if _DOTENV_PATH.exists():
        for line in _DOTENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_dotenv(data: dict[str, str]) -> None:
    """Write dict to .env file, preserving other keys."""
    existing = _read_dotenv()
    existing.update(data)
    lines = [f"{k}={v}" for k, v in existing.items()]
    _DOTENV_PATH.write_text("\n".join(lines) + "\n")
    _DOTENV_PATH.chmod(0o600)


@router.get("/info")
async def system_info():
    """Return system information for the settings modal."""
    from strategy_research.api.user_db import get_user_db
    from strategy_research.cli.llm_config_check import check_llm_config

    db = get_user_db()

    # Workspace path
    workspace = os.environ.get("SR_WORKSPACE_PATH", str(Path.cwd()))

    # LLM status
    try:
        llm = check_llm_config()
    except Exception:
        llm = {"configured": False, "provider": "unknown", "model": "unknown", "api_key_source": "unknown"}

    # Model info from catalog (context window, capabilities, cost)
    model_info = None
    try:
        from strategy_research.core.llm.model_catalog import get_model_info

        provider = llm.get("provider") or "unknown"
        model = llm.get("model") or "unknown"
        if provider != "unknown":
            info = get_model_info(provider, model)
            model_info = info.to_dict()
    except Exception as exc:
        logger.debug("Failed to load model info: %s", exc)

    return {
        "workspace_path": workspace,
        "user_count": db.user_count(),
        "llm": llm,
        "model_info": model_info,
    }


class ModelInfoRefreshRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


@router.post("/model-info/refresh")
async def refresh_model_info(body: ModelInfoRefreshRequest):
    """Force-refresh model metadata from models.dev.

    Body is optional; if omitted, refreshes the currently configured
    provider/model. Returns the ModelInfo that was written to cache.
    """
    from strategy_research.cli.llm_config_check import check_llm_config
    from strategy_research.core.llm.model_catalog import refresh_model_info

    provider = body.provider
    model = body.model
    if not provider or not model or provider == "unknown":
        try:
            llm = check_llm_config()
            provider = provider or llm.get("provider")
            model = model or llm.get("model")
        except Exception:
            pass

    if not provider or not model or provider == "unknown" or model == "unknown":
        raise HTTPException(
            status_code=400,
            detail="provider and model are required (or configure LLM first)",
        )

    try:
        info = await refresh_model_info(provider, model)
        return info.to_dict()
    except Exception as exc:
        logger.exception("Model info refresh failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/llm")
async def get_llm_config():
    """Get current LLM configuration."""
    config = {"provider": "", "model": "", "api_key": "", "base_url": ""}

    if _LLM_JSON_PATH.exists():
        try:
            data = json.loads(_LLM_JSON_PATH.read_text())
            llm = data.get("llm", {})
            config["provider"] = llm.get("provider", "")
            config["model"] = llm.get("model", "")
            config["base_url"] = llm.get("base_url", "")
            # Mask API key
            api_key_ref = llm.get("api_key", "")
            if api_key_ref.startswith("env:"):
                env_var = api_key_ref[4:]
                dotenv = _read_dotenv()
                real_key = dotenv.get(env_var, "")
                config["api_key"] = real_key[:4] + "••••••••" + real_key[-4:] if len(real_key) > 8 else "••••"
            else:
                config["api_key"] = api_key_ref[:4] + "••••••••" if api_key_ref else ""
        except Exception:
            pass

    return config


@router.put("/llm")
async def update_llm_config(body: LLMConfigUpdate):
    """Update LLM configuration (writes to ~/.quantnodes/llm.json + .env)."""
    _QUANTNODES_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing config
    existing = {}
    if _LLM_JSON_PATH.exists():
        try:
            existing = json.loads(_LLM_JSON_PATH.read_text())
        except Exception:
            pass

    llm = existing.get("llm", {})

    # Provider env var mapping
    provider_env_map = {
        "minimax": "LLM_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    # Update fields
    if body.provider is not None:
        llm["provider"] = body.provider
    if body.model is not None:
        llm["model"] = body.model
    if body.base_url is not None:
        llm["base_url"] = body.base_url
    if body.api_key is not None:
        provider = llm.get("provider", "minimax")
        env_var = provider_env_map.get(provider, "LLM_API_KEY")
        llm["api_key"] = f"env:{env_var}"
        _write_dotenv({env_var: body.api_key})

    # Set defaults
    llm.setdefault("enabled", True)
    llm.setdefault("timeout", 300)
    llm.setdefault("max_retries", 2)

    existing["llm"] = llm
    _LLM_JSON_PATH.write_text(json.dumps(existing, indent=2) + "\n")

    return {"status": "ok", "provider": llm.get("provider"), "model": llm.get("model")}
