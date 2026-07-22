"""MetricsLogger — 写入指标记录。

JSONL 格式存储，支持实时统计。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WriteMetric:
    """单次写入指标。"""

    timestamp: float
    count: int
    duration: float
    rate: float
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MetricsLogger:
    """写入指标记录器。

    Args:
        log_path: JSONL 日志文件路径，None 则仅内存记录。
        max_memory_metrics: 内存中保留的最大指标数。
    """

    def __init__(
        self,
        log_path: Path | str | None = None,
        max_memory_metrics: int = 10_000,
    ) -> None:
        self._log_path = Path(log_path) if log_path else None
        self._max_memory_metrics = max_memory_metrics
        self._metrics: list[WriteMetric] = []

        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def record_write(
        self,
        count: int,
        duration: float,
        success: bool,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WriteMetric:
        """记录写入指标。

        Args:
            count: 写入条数。
            duration: 耗时（秒）。
            success: 是否成功。
            error: 错误信息。
            metadata: 额外元数据。

        Returns:
            记录的指标对象。
        """
        rate = count / duration if duration > 0 else 0.0

        metric = WriteMetric(
            timestamp=time.time(),
            count=count,
            duration=duration,
            rate=rate,
            success=success,
            error=error,
            metadata=metadata or {},
        )

        self._metrics.append(metric)

        # 限制内存使用
        if len(self._metrics) > self._max_memory_metrics:
            self._metrics = self._metrics[-self._max_memory_metrics:]

        # 写入 JSONL
        if self._log_path:
            self._write_to_log(metric)

        return metric

    def _write_to_log(self, metric: WriteMetric) -> None:
        """写入 JSONL 日志。"""
        entry = {
            "ts": metric.timestamp,
            "count": metric.count,
            "duration": metric.duration,
            "rate": metric.rate,
            "ok": metric.success,
        }
        if metric.error:
            entry["error"] = metric.error
        if metric.metadata:
            entry["meta"] = metric.metadata

        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # 日志写入失败不影响主流程

    def get_stats(self) -> dict:
        """获取统计信息。"""
        if not self._metrics:
            return {
                "total_writes": 0,
                "total_messages": 0,
                "success_rate": 0.0,
                "avg_rate": 0.0,
                "max_rate": 0.0,
                "min_rate": 0.0,
                "avg_duration": 0.0,
                "total_duration": 0.0,
            }

        successful = [m for m in self._metrics if m.success]
        rates = [m.rate for m in successful]
        durations = [m.duration for m in self._metrics]

        return {
            "total_writes": len(self._metrics),
            "total_messages": sum(m.count for m in self._metrics),
            "success_rate": len(successful) / len(self._metrics) if self._metrics else 0.0,
            "avg_rate": sum(rates) / len(rates) if rates else 0.0,
            "max_rate": max(rates) if rates else 0.0,
            "min_rate": min(rates) if rates else 0.0,
            "avg_duration": sum(durations) / len(durations) if durations else 0.0,
            "total_duration": sum(durations),
        }

    def get_recent(self, n: int = 10) -> list[dict]:
        """获取最近 n 条指标。"""
        recent = self._metrics[-n:]
        return [
            {
                "ts": m.timestamp,
                "count": m.count,
                "duration": m.duration,
                "rate": m.rate,
                "ok": m.success,
            }
            for m in recent
        ]

    def clear(self) -> None:
        """清空内存中的指标。"""
        self._metrics.clear()
