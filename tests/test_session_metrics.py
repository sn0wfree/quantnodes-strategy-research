import pytest
import json
import time
from pathlib import Path
from strategy_research.core.session.metrics import MetricsLogger


class TestMetricsLogger:
    def test_record_write(self):
        logger = MetricsLogger()
        metric = logger.record_write(count=100, duration=1.0, success=True)
        assert metric.count == 100
        assert metric.duration == 1.0
        assert metric.rate == 100.0
        assert metric.success is True

    def test_record_write_with_error(self):
        logger = MetricsLogger()
        metric = logger.record_write(count=10, duration=0.5, success=False, error="test error")
        assert metric.success is False
        assert metric.error == "test error"

    def test_get_stats(self):
        logger = MetricsLogger()
        logger.record_write(count=100, duration=1.0, success=True)
        logger.record_write(count=200, duration=2.0, success=True)
        stats = logger.get_stats()
        assert stats["total_writes"] == 2
        assert stats["total_messages"] == 300
        assert stats["success_rate"] == 1.0
        assert stats["avg_rate"] == 100.0

    def test_get_stats_empty(self):
        logger = MetricsLogger()
        stats = logger.get_stats()
        assert stats["total_writes"] == 0

    def test_get_recent(self):
        logger = MetricsLogger()
        for i in range(10):
            logger.record_write(count=i, duration=0.1, success=True)
        recent = logger.get_recent(n=3)
        assert len(recent) == 3
        assert recent[-1]["count"] == 9

    def test_clear(self):
        logger = MetricsLogger()
        logger.record_write(count=100, duration=1.0, success=True)
        logger.clear()
        stats = logger.get_stats()
        assert stats["total_writes"] == 0

    def test_jsonl_log(self, tmp_path):
        log_path = tmp_path / "metrics.jsonl"
        logger = MetricsLogger(log_path=log_path)
        logger.record_write(count=100, duration=1.0, success=True)
        logger.record_write(count=200, duration=2.0, success=False, error="fail")

        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        assert entry1["count"] == 100
        assert entry1["ok"] is True

        entry2 = json.loads(lines[1])
        assert entry2["count"] == 200
        assert entry2["ok"] is False
        assert entry2["error"] == "fail"


class TestMetricsLoggerLimits:
    def test_max_memory_metrics(self):
        logger = MetricsLogger(max_memory_metrics=100)
        for i in range(150):
            logger.record_write(count=i, duration=0.1, success=True)
        stats = logger.get_stats()
        assert stats["total_writes"] == 100  # Only last 100 kept

    def test_rate_calculation(self):
        logger = MetricsLogger()
        logger.record_write(count=1000, duration=1.0, success=True)
        stats = logger.get_stats()
        assert stats["avg_rate"] == 1000.0
        assert stats["max_rate"] == 1000.0
        assert stats["min_rate"] == 1000.0
