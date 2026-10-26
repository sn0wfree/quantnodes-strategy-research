"""预算执行点（A1 运行中门）—— 所有 LLM 调用的唯一预算入口。

A5 不变量 INV-9：**任何 LLM 调用必须经过 plan_call()**。
- react 节点每迭代调用一次（运行中检查）
- llm 节点调用一次（单次调用前检查）
- 未来任何新节点同样必须调用——这是「预算绕不过去」的结构保证

为什么单独成模块（而非散落各节点）：审计门①发现 llm 节点曾直接调
client.chat 而绕过预算检查。把检查收口到一处，节点只负责「问能不能调」。

与 budget_gate.py 的分层：
- budget_gate：**session 层**（chat 准入的静态额度合并）
- 本模块：**run 层运行时**（已消费量 vs 上限，每次调用前判定）
- strategy/budget.py：已消费量的记账（record_usage）与三维判定
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from strategy_research.core.agent.strategy.budget import (
    BudgetLimits,
    BudgetUsage,
    evaluate_budget,
    record_usage,
)


@dataclass
class RunBudget:
    """单 run 的预算状态（记账 + 判定）。"""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    usage: BudgetUsage = field(default_factory=lambda: BudgetUsage(started_at=time.perf_counter()))
    exhausted: bool = False
    reason: str | None = None

    def plan_call(self) -> tuple[bool, str | None]:
        """LLM 调用前的唯一检查点。返回 (allowed, reason)。"""
        exceeded, reason = evaluate_budget(self.usage, self.limits)
        if exceeded:
            self.exhausted = True
            self.reason = reason
            return False, reason
        return True, None

    def record(self, response_usage: dict | None) -> None:
        """调用后记账（在 plan_call 之后、prompt 追加之前调用）。"""
        self.usage = record_usage(self.usage, self.limits, response_usage or {})

    def snapshot(self) -> dict:
        return {
            "total_tokens": self.usage.tokens,
            "cost_usd": round(self.usage.cost_usd, 6),
            "exhausted": self.exhausted,
            "reason": self.reason,
        }


INV_LLM_BUDGET = "INV-9: 所有 LLM 调用必须经过 RunBudget.plan_call（预算不可绕过）"
