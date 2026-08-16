"""Tests for LLMConfigBuilder (Phase 2.4)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.llm.builder import LLMConfigBuilder
from strategy_research.core.llm.config import LLMConfig


class TestLLMConfigBuilderBasics(unittest.TestCase):

    def test_empty_builder_returns_dict(self):
        # No defaults registered → empty dict
        builder = LLMConfigBuilder()
        result = builder.build()
        self.assertIsInstance(result, dict)

    def test_with_code_defaults(self):
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .build())
        # Should have at least base_url and model from LLMConfig defaults
        self.assertIn("base_url", result)
        self.assertIn("model", result)

    def test_with_cli_overrides(self):
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({"llm_model": "gpt-4", "llm_temperature": 0.5})
                  .build())
        self.assertEqual(result["model"], "gpt-4")
        self.assertEqual(result["temperature"], 0.5)

    def test_cli_only_truthy_keys_applied(self):
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({"llm_model": "gpt-4", "llm_temperature": None})
                  .build())
        # None values are dropped
        self.assertEqual(result["model"], "gpt-4")
        # temperature should be the default, not None
        self.assertIn("temperature", result)

    def test_cli_only_llm_prefix_applied(self):
        """Non-llm_ keys are ignored."""
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({
                      "llm_model": "gpt-4",
                      "model": "should-be-ignored",
                      "verbose": True,
                  })
                  .build())
        self.assertEqual(result["model"], "gpt-4")

    def test_with_env_overrides(self):
        env = {"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "https://example.com"}
        result = (LLMConfigBuilder(env=env)
                  .with_code_defaults()
                  .with_env_overrides(env)
                  .build())
        self.assertEqual(result["base_url"], "https://example.com")


class TestLLMConfigBuilderProviderFallback(unittest.TestCase):

    def test_provider_fallback_when_missing(self):
        # with_bridge_yaml is not called → no provider set in the merge,
        # so defaults should remain.
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .build())
        self.assertEqual(result["base_url"], LLMConfig().base_url)

    def test_provider_fallback_applied_when_set(self):
        # Set provider via cli, base_url and model should fall back to defaults.
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({"llm_provider": "openai"})
                  .build())
        # openai default base_url
        self.assertEqual(result["base_url"], "https://api.openai.com/v1")


class TestLLMConfigBuilderApiKey(unittest.TestCase):

    def test_api_key_from_openai_env(self):
        env = {"OPENAI_API_KEY": "sk-openai-123"}
        result = (LLMConfigBuilder(env=env)
                  .with_code_defaults()
                  .build())
        self.assertEqual(result["api_key"], "sk-openai-123")

    def test_api_key_from_llm_api_key_env(self):
        env = {"LLM_API_KEY": "sk-llm-456"}
        result = (LLMConfigBuilder(env=env)
                  .with_code_defaults()
                  .build())
        self.assertEqual(result["api_key"], "sk-llm-456")

    def test_api_key_priority_openai_over_llm(self):
        env = {"OPENAI_API_KEY": "sk-openai", "LLM_API_KEY": "sk-llm"}
        result = (LLMConfigBuilder(env=env)
                  .with_code_defaults()
                  .build())
        self.assertEqual(result["api_key"], "sk-openai")


class TestLLMConfigBuilderComposition(unittest.TestCase):

    def test_layer_priority(self):
        """CLI > env > bridge > defaults."""
        env = {"OPENAI_BASE_URL": "https://env.example.com"}
        result = (LLMConfigBuilder(env=env)
                  .with_code_defaults()
                  .with_env_overrides(env)
                  .with_cli_overrides({"llm_base_url": "https://cli.example.com"})
                  .build())
        self.assertEqual(result["base_url"], "https://cli.example.com")

    def test_custom_post_hook(self):
        """Custom post-hooks run after the standard ones."""
        calls = []

        def trace(d):
            calls.append("custom")
            return d

        (LLMConfigBuilder()
         .with_code_defaults()
         .with_post_hook(trace)
         .build())
        self.assertIn("custom", calls)

    def test_returns_dict_with_dataclass_fields(self):
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({"llm_model": "gpt-4"})
                  .build())
        # Result should contain standard LLMConfig field names
        for key in ("base_url", "model", "temperature"):
            self.assertIn(key, result)


class TestBuilderIntegration(unittest.TestCase):

    def test_full_chain_produces_valid_config(self):
        """A full builder chain should produce something convertible to LLMConfig."""
        import dataclasses as _dc
        result = (LLMConfigBuilder()
                  .with_code_defaults()
                  .with_cli_overrides({
                      "llm_model": "gpt-4o-mini",
                      "llm_temperature": 0.3,
                  })
                  .build())
        # Should be constructible as LLMConfig
        valid_fields = {f.name for f in _dc.fields(LLMConfig)}
        kwargs = {k: v for k, v in result.items() if k in valid_fields}
        cfg = LLMConfig(**kwargs)
        self.assertEqual(cfg.model, "gpt-4o-mini")
        self.assertEqual(cfg.temperature, 0.3)


if __name__ == "__main__":
    unittest.main()
