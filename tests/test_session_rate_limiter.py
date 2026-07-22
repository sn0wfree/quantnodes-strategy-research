import pytest
import time
from strategy_research.core.session.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_default_config(self):
        limiter = RateLimiter()
        assert limiter.max_per_second == 80_000

    def test_custom_config(self):
        limiter = RateLimiter(max_per_second=1000)
        assert limiter.max_per_second == 1000

    def test_set_max_per_second(self):
        limiter = RateLimiter()
        limiter.max_per_second = 5000
        assert limiter.max_per_second == 5000

    def test_set_max_per_second_invalid(self):
        limiter = RateLimiter()
        with pytest.raises(ValueError):
            limiter.max_per_second = 0
        with pytest.raises(ValueError):
            limiter.max_per_second = -1

    def test_acquire_within_limit(self):
        limiter = RateLimiter(max_per_second=100)
        wait_time = limiter.acquire(50)
        assert wait_time == 0.0

    def test_acquire_exceeds_limit(self):
        limiter = RateLimiter(max_per_second=10)
        limiter.acquire(10)
        wait_time = limiter.acquire(1)
        assert wait_time > 0

    def test_acquire_zero(self):
        limiter = RateLimiter()
        wait_time = limiter.acquire(0)
        assert wait_time == 0.0

    def test_reset(self):
        limiter = RateLimiter(max_per_second=10)
        limiter.acquire(10)
        limiter.reset()
        wait_time = limiter.acquire(1)
        assert wait_time == 0.0

    def test_get_stats(self):
        limiter = RateLimiter()
        limiter.acquire(100)
        stats = limiter.get_stats()
        assert stats["total_requests"] == 1
        assert stats["current_count"] == 100

    def test_window_reset(self):
        limiter = RateLimiter(max_per_second=10)
        limiter.acquire(10)
        time.sleep(1.1)
        wait_time = limiter.acquire(1)
        assert wait_time == 0.0


class TestRateLimiterPerformance:
    def test_burst_within_second(self):
        limiter = RateLimiter(max_per_second=80000)
        start = time.time()
        for _ in range(80000):
            limiter.acquire(1)
        elapsed = time.time() - start
        # Should not wait within the second
        assert elapsed < 0.1

    def test_burst_exceeds_second(self):
        limiter = RateLimiter(max_per_second=100)
        limiter.acquire(100)
        # Window is still active, next acquire should wait
        wait_time = limiter.acquire(1)
        # The window resets and returns wait_time = 1.0 - elapsed
        # Since elapsed is ~0, wait_time should be ~1.0
        assert wait_time >= 0.9
