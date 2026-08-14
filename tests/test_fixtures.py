"""Tests for the shared test fixtures (Phase 1.3)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures import (
    MockLLMClient,
    make_alpha_panel,
    make_argv,
    make_mock_chat_response,
    make_mock_stream_chunks,
    make_ohlcv_panel,
    make_panel,
    make_random_prices,
    make_random_returns,
    make_test_session_id,
    make_test_user_id,
    run_async,
)
from _fixtures.alpha import make_minimal_alpha_panel


class TestMakeRandomPrices(unittest.TestCase):

    def test_shape(self):
        prices = make_random_prices(n_days=100, n_assets=5)
        self.assertEqual(prices.shape, (100, 5))

    def test_index_is_datetime(self):
        prices = make_random_prices(n_days=10)
        self.assertIsInstance(prices.index, pd.DatetimeIndex)

    def test_columns_named(self):
        prices = make_random_prices(n_assets=3)
        self.assertEqual(list(prices.columns), ["S0", "S1", "S2"])

    def test_reproducible(self):
        a = make_random_prices(seed=7)
        b = make_random_prices(seed=7)
        pd.testing.assert_frame_equal(a, b)

    def test_custom_start(self):
        prices = make_random_prices(n_days=10, start=42.0)
        # First price ~ start (within random drift)
        self.assertGreater(prices.iloc[0, 0], 0)


class TestMakeRandomReturns(unittest.TestCase):

    def test_shape(self):
        r = make_random_returns(n_days=100, n_assets=5)
        self.assertEqual(r.shape, (100, 5))

    def test_zero_mean(self):
        r = make_random_returns(n_days=10000, seed=0, mean=0.0, std=0.01)
        self.assertAlmostEqual(r.values.mean(), 0.0, places=2)


class TestMakeOhlcvPanel(unittest.TestCase):

    def test_returns_dict(self):
        panel = make_ohlcv_panel(n_days=50)
        self.assertIsInstance(panel, dict)

    def test_required_columns(self):
        panel = make_ohlcv_panel(n_days=50)
        for col in ("open", "high", "low", "close", "volume"):
            self.assertIn(col, panel)

    def test_all_frames_same_shape(self):
        panel = make_ohlcv_panel(n_days=50, n_assets=4)
        first_shape = next(iter(panel.values())).shape
        for col, df in panel.items():
            self.assertEqual(df.shape, first_shape, f"shape mismatch in {col}")

    def test_derived_columns_when_enabled(self):
        panel = make_ohlcv_panel(n_days=200, include_derived=True)
        self.assertIn("adv20", panel)
        self.assertIn("adv60", panel)

    def test_no_derived_when_disabled(self):
        panel = make_ohlcv_panel(n_days=50, include_derived=False)
        self.assertNotIn("adv20", panel)

    def test_alias(self):
        # make_panel and make_ohlcv_panel produce identical results for
        # the same seed (same default include_derived).
        a = make_panel(n_days=20, n_assets=2, seed=42)
        b = make_ohlcv_panel(n_days=20, n_assets=2, seed=42)
        self.assertEqual(set(a.keys()), set(b.keys()))


class TestMakeAlphaPanel(unittest.TestCase):

    def test_basic(self):
        panel = make_alpha_panel(n_days=50, n_assets=3)
        self.assertIn("close", panel)
        self.assertIn("returns", panel)

    def test_with_fundamentals(self):
        panel = make_alpha_panel(n_days=20, n_assets=2, with_fundamentals=True)
        for col in (
            "fund:roe",
            "fund:gross_profitability",
            "fund:asset_growth",
            "fund:net_income",
            "fund:shares_diluted",
        ):
            self.assertIn(col, panel)

    def test_without_fundamentals(self):
        panel = make_alpha_panel(n_days=20, n_assets=2, with_fundamentals=False)
        self.assertNotIn("fund:roe", panel)

    def test_minimal_panel(self):
        panel = make_minimal_alpha_panel()
        self.assertIn("close", panel)


class TestSessionIds(unittest.TestCase):

    def test_unique_session_ids(self):
        a = make_test_session_id()
        b = make_test_session_id()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("test_"))
        self.assertTrue(b.startswith("test_"))

    def test_unique_user_ids(self):
        a = make_test_user_id()
        b = make_test_user_id()
        self.assertNotEqual(a, b)

    def test_custom_prefix(self):
        sid = make_test_session_id(prefix="demo")
        self.assertTrue(sid.startswith("demo_"))


class TestMockLLMClient(unittest.TestCase):

    def test_default_response(self):
        client = MockLLMClient('{"passed": true}')
        resp = client.chat(messages=[{"role": "user", "content": "x"}])
        self.assertEqual(resp.content, '{"passed": true}')

    def test_call_count(self):
        client = MockLLMClient("hi")
        client.chat(messages=[{"role": "user", "content": "1"}])
        client.chat(messages=[{"role": "user", "content": "2"}])
        self.assertEqual(client.call_count, 2)

    def test_captures_messages(self):
        client = MockLLMClient("hi")
        msgs = [{"role": "user", "content": "hello"}]
        client.chat(messages=msgs)
        self.assertEqual(client.last_messages, msgs)

    def test_chat_error(self):
        client = MockLLMClient(chat_error=RuntimeError("net"))
        with self.assertRaises(RuntimeError):
            client.chat(messages=[])

    def test_response_factory(self):
        client = MockLLMClient(response_factory=lambda msgs, **k: "factory")
        self.assertEqual(client.chat(messages=[]).content, "factory")

    def test_achat(self):
        client = MockLLMClient("hello")
        result = asyncio.run(client.achat(messages=[]))
        self.assertEqual(result.content, "hello")


class TestMakeMockChatResponse(unittest.TestCase):

    def test_basic(self):
        r = make_mock_chat_response("hello")
        self.assertEqual(r["choices"][0]["message"]["content"], "hello")

    def test_tool_calls(self):
        r = make_mock_chat_response("x", tool_calls=[{"id": "t1"}])
        self.assertIn("tool_calls", r["choices"][0]["message"])

    def test_default_usage(self):
        r = make_mock_chat_response("x")
        self.assertEqual(r["usage"]["total_tokens"], 0)


class TestMakeMockStreamChunks(unittest.TestCase):

    def test_basic(self):
        chunks = make_mock_stream_chunks(["a", "b", "c"])
        self.assertEqual(len(chunks), 4)  # 3 + [DONE]
        self.assertTrue(chunks[-1].startswith("data: [DONE]"))

    def test_finish_reason_last(self):
        chunks = make_mock_stream_chunks(["a", "b"])
        import json
        last = json.loads(chunks[-2].replace("data: ", ""))
        self.assertEqual(last["choices"][0]["finish_reason"], "stop")

    def test_reasoning_field(self):
        chunks = make_mock_stream_chunks(["a"], reasoning=["thinking"])
        import json
        first = json.loads(chunks[0].replace("data: ", ""))
        self.assertEqual(first["choices"][0]["delta"]["reasoning_content"], "thinking")


class TestMakeArgv(unittest.TestCase):

    def test_basic(self):
        argv = make_argv("session", "list")
        self.assertEqual(argv, ["prog", "session", "list"])

    def test_empty(self):
        self.assertEqual(make_argv(), ["prog"])


class TestRunAsync(unittest.TestCase):

    def test_runs_coroutine(self):
        async def coro():
            return 42
        self.assertEqual(run_async(coro()), 42)


if __name__ == "__main__":
    unittest.main()
