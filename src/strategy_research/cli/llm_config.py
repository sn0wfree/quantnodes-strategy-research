"""Extracted from cli.py — LLM configuration helpers.

Contains:
- _LLM_PARENT (argparse parent parser for --llm-* flags)
- _cli_overrides_from_args
- build_llm_config
- _cmd_llm_list_profiles
"""

from __future__ import annotations

import argparse


_LLM_PARENT = argparse.ArgumentParser(
    add_help=False,
    prog="quantnodes-research (LLM flags)",
    description="LLM configuration overrides",
)
_llm_g = _LLM_PARENT.add_argument_group("LLM configuration")
_llm_g.add_argument("--llm-profile", default=None,
                    help="激活的 LLM profile (从 ~/.quantnodes-research/llm.yaml)")
_llm_g.add_argument("--llm-model", default=None, help="覆盖 model")
_llm_g.add_argument("--llm-base-url", default=None, help="覆盖 base_url")
_llm_g.add_argument("--llm-temperature", type=float, default=None,
                    help="覆盖 temperature")
_llm_g.add_argument("--llm-max-tokens", type=int, default=None,
                    help="覆盖 max_tokens")
_llm_g.add_argument("--llm-top-p", type=float, default=None, help="覆盖 top_p")
_llm_g.add_argument("--llm-timeout", type=float, default=None,
                    help="覆盖 timeout_s")
_llm_g.add_argument("--llm-max-retries", type=int, default=None,
                    help="覆盖 max_retries")
_llm_g.add_argument("--llm-seed", type=int, default=None, help="覆盖 seed")
_llm_g.add_argument("--llm-stream", dest="llm_stream",
                    action="store_true", default=None, help="强制流式")
_llm_g.add_argument("--llm-no-stream", dest="llm_stream",
                    action="store_false", help="禁用流式")


def _cli_overrides_from_args(args: argparse.Namespace | None) -> dict:
    """Extract --llm-* kwargs from argparse Namespace."""
    if args is None:
        return {}
    out = {}
    for key, value in vars(args).items():
        if key.startswith("llm_") and value is not None:
            out[key] = value
    return out


def build_llm_config(args: argparse.Namespace | None = None,
                     *, profile: str | None = None,
                     cli_overrides: dict | None = None) -> "LLMConfig":
    """Build an LLMConfig from CLI args + 4-layer merge.

    Args:
        args: argparse Namespace (with --llm-* attributes).
        profile: Explicit profile name override (highest priority).
        cli_overrides: Explicit override dict (alternative to args).

    Returns:
        Fully merged LLMConfig.
    """
    from strategy_research.core.llm import LLMConfig
    overrides = cli_overrides if cli_overrides is not None else _cli_overrides_from_args(args)
    return LLMConfig.load(profile=profile, cli_overrides=overrides)


def _cmd_llm_list_profiles() -> int:
    """Print all available LLM profiles from yaml config."""
    from strategy_research.core.llm.config import (
        DEFAULT_LLM_CONFIG_PATH,
        get_default_profile,
        list_profiles,
    )
    profiles = list_profiles()
    default = get_default_profile()
    print(f"# LLM profiles from {DEFAULT_LLM_CONFIG_PATH}")
    if not profiles:
        print("(no llm.yaml found — using code defaults)")
    else:
        for name in profiles:
            marker = " *" if name == default else ""
            print(f"  {name}{marker}")
        print(f"\ndefault: {default}")
    return 0
