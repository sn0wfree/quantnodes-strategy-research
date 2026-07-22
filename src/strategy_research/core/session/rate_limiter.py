"""RateLimiter — 写入限流器。

可配置阈值，默认 80,000 条/秒（基于性能测试峰值 ~82,978 条/秒）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimiterStats:
    """限流统计。"""

    total_requests: int = 0
    total_waited: int = 0
    total_wait_time: float = 0.0
    current_count: int = 0
    window_start: float = field(default_factory=time.time)


class RateLimiter:
    """写入限流器。

    使用滑动窗口算法，限制每秒写入次数。

    Args:
        max_per_second: 每秒最大写入次数，默认 80,000。
    """

    def __init__(self, max_per_second: int = 80_000) -> None:
        self._max_per_second = max_per_second
        self._current_count = 0
        self._window_start = time.time()
        self._stats = RateLimiterStats()

    @property
    def max_per_second(self) -> int:
        """每秒最大写入次数。"""
        return self._max_per_second

    @max_per_second.setter
    def max_per_second(self, value: int) -> None:
        """设置每秒最大写入次数。"""
        if value <= 0:
            raise ValueError("max_per_second must be positive")
        self._max_per_second = value

    def acquire(self, count: int = 1) -> float:
        """获取写入许可。

        Args:
            count: 请求的写入次数。

        Returns:
            需要等待的时间（秒），0 表示无需等待。
        """
        if count <= 0:
            return 0.0

        self._stats.total_requests += 1
        now = time.time()
        elapsed = now - self._window_start

        # 窗口已过期，重置
        if elapsed >= 1.0:
            self._current_count = 0
            self._window_start = now
            self._current_count += count
            return 0.0

        # 检查是否超过限制
        if self._current_count + count > self._max_per_second:
            wait_time = 1.0 - elapsed
            self._stats.total_waited += 1
            self._stats.total_wait_time += wait_time
            self._current_count = count
            self._window_start = time.time()
            return wait_time

        self._current_count += count
        return 0.0

    def get_stats(self) -> dict:
        """获取统计信息。"""
        return {
            "max_per_second": self._max_per_second,
            "current_count": self._current_count,
            "window_start": self._window_start,
            "total_requests": self._stats.total_requests,
            "total_waited": self._stats.total_waited,
            "total_wait_time": self._stats.total_wait_time,
        }

    def reset(self) -> None:
        """重置限流器。"""
        self._current_count = 0
        self._window_start = time.time()
        self._stats = RateLimiterStats()
