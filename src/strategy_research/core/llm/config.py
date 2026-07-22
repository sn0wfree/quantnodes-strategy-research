"""LLMConfig — immutable LLM configuration with 4-layer merge.

Layers (high priority overrides low):
    1. CLI overrides      (argparse namespace dict)
    2. Environment vars   (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
                           STRATEGY_RESEARCH_LLM_PROFILE)
    3. YAML profile       (~/.quantnodes-research/llm.yaml)
    4. Code defaults      (dataclass field defaults)

Design notes:
    - frozen=True: every override returns a NEW instance (no mutation)
    - API key never comes from YAML (always env var)
    - YAML schema is intentionally flat inside each profile
    - .env loading is best-effort (python-dotenv is optional)
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os
from pathlib import Path
from typing import Any, Mapping

import yaml

logger = logging.getLogger(__name__)

# ── Public constants ────────────────────────────────────────────────

DEFAULT_LLM_CONFIG_PATH = Path.home() / ".quantnodes-research" / "llm.yaml"

# Env var names (only these are read from environment)
ENV_API_KEY = "OPENAI_API_KEY"
ENV_BASE_URL = "OPENAI_BASE_URL"
ENV_MODEL = "OPENAI_MODEL"
ENV_PROFILE = "STRATEGY_RESEARCH_LLM_PROFILE"
ENV_CONFIG_PATH = "STRATEGY_RESEARCH_LLM_CONFIG"

# Supported providers (just used for hinting/defaults; any string OK)
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai":   {"base_url": "https://api.openai.com/v1",
                 "model": "gpt-4o-mini"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat"},
    "kimi":     {"base_url": "https://api.moonshot.cn/v1",
                 "model": "moonshot-v1-8k"},
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "model": "qwen-plus"},
}


# ── LLMConfig dataclass ─────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class LLMConfig:
    """Immutable LLM configuration.

    Construct directly, or use LLMConfig.load() to apply 4-layer merge.
    Use .with_config(**kwargs) to derive a tweaked instance.
    """

    # ── Endpoint ─────────────────────────────────
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""                              # only from env (OPENAI_API_KEY)
    model: str = "gpt-4o-mini"
    provider: str = "auto"                         # auto|openai|deepseek|kimi|qwen|custom

    # ── Sampling ────────────────────────────────
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: tuple[str, ...] | None = None
    seed: int | None = None

    # ── Network ──────────────────────────────────
    timeout_s: float = 60.0
    max_retries: int = 3
    retry_backoff_s: float = 1.0
    proxy: str | None = None

    # ── Behaviour ───────────────────────────────
    stream: bool = True
    parallel_tool_calls: bool = True
    tool_choice: str = "auto"                      # auto|required|none|{"name":..}

    # ── Meta ────────────────────────────────────
    profile: str = "default"

    # ── Methods ──────────────────────────────────

    def with_config(self, **kwargs: Any) -> "LLMConfig":
        """Return a new LLMConfig with the given fields overridden."""
        return dataclasses.replace(self, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation (api_key included for debug)."""
        d = dataclasses.asdict(self)
        return d

    def masked_dict(self) -> dict[str, Any]:
        """Return a dict with api_key masked (for logging)."""
        d = self.to_dict()
        if d.get("api_key"):
            key = d["api_key"]
            if len(key) > 8:
                d["api_key"] = key[:4] + "***" + key[-4:]
            else:
                d["api_key"] = "***"
        return d

    # ── Factory ──────────────────────────────────

    @classmethod
    def load(
        cls,
        *,
        profile: str | None = None,
        cli_overrides: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        yaml_path: Path | None = None,
        load_dotenv: bool = True,
    ) -> "LLMConfig":
        """Construct an LLMConfig by merging 4 layers.

        Args:
            profile:      Override profile name (priority over env STRATEGY_RESEARCH_LLM_PROFILE).
            cli_overrides: argparse-namespace dict (e.g. vars(args)); only truthy keys applied.
            env:           Custom env mapping (defaults to os.environ).
            yaml_path:     Override path to llm.yaml (priority over env STRATEGY_RESEARCH_LLM_CONFIG).
            load_dotenv:   Whether to call dotenv.load_dotenv() (no-op if not installed).

        Returns:
            Fully merged LLMConfig instance.

        Raises:
            ValueError: If specified profile does not exist in YAML.
            yaml.YAMLError: If YAML file is malformed (transparent from PyYAML).
            FileNotFoundError: Not raised; missing YAML is silent.
        """
        env_map = dict(env if env is not None else os.environ)

        if load_dotenv:
            _try_load_dotenv()

        # 1) Code defaults
        cfg = cls()

        # Resolve effective yaml_path & profile
        eff_yaml_path = Path(yaml_path) if yaml_path is not None else _resolve_yaml_path(env_map)

        # 2) YAML profile layer
        yaml_default_profile = _yaml_default_profile(eff_yaml_path) if eff_yaml_path.exists() else "default"

        eff_profile = (
            profile
            or env_map.get(ENV_PROFILE)
            or yaml_default_profile
            or cfg.profile
        )

        # Load yaml profile if file exists.
        # Silent fallback ONLY when: file missing OR (no explicit profile AND implicit
        #   resolution picked a profile that doesn't exist). Structural errors always raise.
        if eff_yaml_path.exists():
            try:
                yaml_data = _load_yaml_profile(eff_yaml_path, eff_profile)
            except ValueError as exc:
                msg = str(exc)
                # "profile not found" is recoverable silently when profile was implicit
                is_not_found = "not found" in msg
                if profile is not None or not is_not_found:
                    raise
                yaml_data = {}  # implicit + profile missing → silent fallback
            if yaml_data:
                cfg = cfg._merge_flat(yaml_data)
                cfg = cfg.with_config(profile=eff_profile)
        # else: no yaml → keep code defaults

        # 3) Env var layer (only the documented env vars)
        env_overrides = _env_to_overrides(env_map)
        if env_overrides:
            cfg = cfg._merge_flat(env_overrides)

        # 4) CLI override layer (only truthy keys; values pass through as-is)
        if cli_overrides:
            cli_flat = _cli_to_overrides(cli_overrides)
            if cli_flat:
                cfg = cfg._merge_flat(cli_flat)

        # api_key is loaded separately from env (never from yaml)
        if not cfg.api_key:
            cfg = cfg.with_config(api_key=load_api_key_from_env(env_map))

        return cfg

    # ── Internal helpers ─────────────────────────

    def _merge_flat(self, data: Mapping[str, Any]) -> "LLMConfig":
        """Return new LLMConfig with dataclass fields updated from data.

        Unknown keys are silently ignored (forward-compat with new fields).
        Tuple fields (stop) are reconstructed as tuple.
        """
        valid_fields = {f.name for f in dataclasses.fields(self)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key in valid_fields and value is not None:
                if key == "stop" and isinstance(value, list):
                    kwargs[key] = tuple(value)
                else:
                    kwargs[key] = value
        if not kwargs:
            return self
        return dataclasses.replace(self, **kwargs)


# ── Helpers ─────────────────────────────────────────────────────────


def _resolve_yaml_path(env: Mapping[str, str]) -> Path:
    """Resolve config path: STRATEGY_RESEARCH_LLM_CONFIG > default."""
    p = env.get(ENV_CONFIG_PATH)
    if p:
        return Path(p).expanduser()
    return DEFAULT_LLM_CONFIG_PATH


def _try_load_dotenv() -> None:
    """Best-effort .env load; no-op if python-dotenv is not installed."""
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        logger.debug("python-dotenv not installed; skipping .env load")
        return
    try:
        _ld()  # load from cwd by default
    except Exception as exc:                       # noqa: BLE001
        logger.debug("dotenv load failed: %s", exc)


def _yaml_default_profile(path: Path) -> str:
    """Read default_profile key from yaml (without raising)."""
    if not path.exists():
        return "default"
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return "default"
    if not isinstance(data, dict):
        return "default"
    default = data.get("default_profile", "default")
    return str(default) if default else "default"


def _load_yaml_profile(path: Path, profile: str | None) -> dict[str, Any]:
    """Load the named profile from a YAML config file.

    Returns empty dict if file missing.
    Raises ValueError on structural errors (root not mapping, profiles wrong type,
    profile not found, profile entry wrong type).
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"llm.yaml root must be a mapping, got {type(data).__name__}")

    # Resolve profile: explicit arg > env > yaml default_profile > "default"
    profiles = data.get("profiles")
    if profiles is None:
        profiles = {}
    elif not isinstance(profiles, dict):
        raise ValueError("llm.yaml 'profiles' must be a mapping")

    if profile is None:
        # No explicit profile → use default_profile from yaml
        profile = data.get("default_profile", "default")

    if profile in profiles:
        chosen = profiles[profile]
    else:
        available = sorted(profiles.keys())
        raise ValueError(
            f"LLM profile '{profile}' not found in {path}; available: {available}"
        )

    if not isinstance(chosen, dict):
        raise ValueError(f"profile '{profile}' must be a mapping")
    return dict(chosen)


def _env_to_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Translate the documented env vars to config overrides.

    Only handles explicit env vars (OPENAI_*, STRATEGY_RESEARCH_LLM_PROFILE).
    Unrelated env vars are ignored.
    """
    overrides: dict[str, Any] = {}
    if ENV_BASE_URL in env:
        overrides["base_url"] = env[ENV_BASE_URL]
    if ENV_MODEL in env:
        overrides["model"] = env[ENV_MODEL]
    if ENV_PROFILE in env:
        overrides["profile"] = env[ENV_PROFILE]
    # api_key handled separately via load() because it's outside the merge path
    return overrides


def _cli_to_overrides(cli: Mapping[str, Any]) -> dict[str, Any]:
    """Translate argparse namespace to config overrides.

    Mapping rule: --llm-foo-bar  →  cli["llm_foo_bar"]
    Only keys starting with 'llm_' and with non-None values are mapped.
    """
    overrides: dict[str, Any] = {}
    for key, value in cli.items():
        if not key.startswith("llm_"):
            continue
        if value is None:
            continue
        # Strip 'llm_' prefix and map to dataclass field name
        field = key[len("llm_"):]
        # Handle special cases
        if field == "stream":
            overrides["stream"] = bool(value)
            continue
        if field == "no_stream":
            overrides["stream"] = not bool(value)
            continue
        if field == "list_profiles":
            # handled separately by CLI; not a config field
            continue
        if field == "temperature":
            overrides["temperature"] = float(value)
        elif field == "max_tokens":
            overrides["max_tokens"] = int(value)
        elif field == "top_p":
            overrides["top_p"] = float(value)
        elif field == "timeout":
            overrides["timeout_s"] = float(value)
        elif field == "max_retries":
            overrides["max_retries"] = int(value)
        elif field == "seed":
            overrides["seed"] = int(value) if value != "" else None
        elif field == "model":
            overrides["model"] = str(value)
        elif field == "base_url":
            overrides["base_url"] = str(value)
        elif field == "profile":
            overrides["profile"] = str(value)
        else:
            # Pass through unknown llm_* keys (forward compat)
            overrides[field] = value
    return overrides


def load_api_key_from_env(env: Mapping[str, str] | None = None) -> str:
    """Load OPENAI_API_KEY from env (api_key is never from yaml)."""
    env_map = env if env is not None else os.environ
    return env_map.get(ENV_API_KEY, "")


def list_profiles(yaml_path: Path | None = None) -> list[str]:
    """List all profile names defined in the yaml config.

    Returns empty list if file missing.
    """
    p = yaml_path if yaml_path is not None else DEFAULT_LLM_CONFIG_PATH
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        return []
    return sorted(profiles.keys())


def get_default_profile(yaml_path: Path | None = None) -> str:
    """Return the default_profile from yaml, or 'default' if missing."""
    p = yaml_path if yaml_path is not None else DEFAULT_LLM_CONFIG_PATH
    if not p.exists():
        return "default"
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return "default"
    if not isinstance(data, dict):
        return "default"
    default = data.get("default_profile", "default")
    return str(default) if default else "default"


def apply_api_key(cfg: LLMConfig, env: Mapping[str, str] | None = None) -> LLMConfig:
    """Return a new LLMConfig with api_key set from env (if cfg.api_key is empty)."""
    if cfg.api_key:
        return cfg
    key = load_api_key_from_env(env)
    if not key:
        return cfg
    return cfg.with_config(api_key=key)