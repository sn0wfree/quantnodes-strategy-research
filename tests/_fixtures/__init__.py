"""Shared test fixtures — public API.

The fixtures module organises reusable test data builders used across the
~300 test files in this repo. Before Phase 1.3 these were inlined as
``np.random.seed(42)`` + ``pd.Series(...).cumsum()`` patterns scattered
throughout test files. Centralising them:

1. Makes tests more readable.
2. Lets us swap one fixture for another (e.g. add fundamental mock columns).
3. Gives a single place to update the data-generation policy (seed, ranges).

Usage:

    from tests._fixtures import make_random_panel, make_ohlcv_panel

    def test_something():
        prices = make_ohlcv_panel(n_days=252, n_assets=5)
        ...
"""

from .market import (
    make_ohlcv_panel,
    make_panel,
    make_random_prices,
    make_random_returns,
)
from .alpha import make_alpha_panel, make_minimal_alpha_panel
from .session import make_test_session_id, make_test_user_id
from .llm import (
    MockLLMClient,
    make_mock_chat_response,
    make_mock_stream_chunks,
)
from .cli import make_argv
from .asyncio import run_async

__all__ = [
    # market
    "make_ohlcv_panel",
    "make_panel",
    "make_random_prices",
    "make_random_returns",
    # alpha
    "make_alpha_panel",
    "make_minimal_alpha_panel",
    # session
    "make_test_session_id",
    "make_test_user_id",
    # llm
    "MockLLMClient",
    "make_mock_chat_response",
    "make_mock_stream_chunks",
    # cli
    "make_argv",
    # asyncio
    "run_async",
]