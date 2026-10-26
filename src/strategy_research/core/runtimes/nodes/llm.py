"""llm 节点（阶段 1a）—— 一次 LLM 调用，把回答追加进消息历史。

预算（A5 INV-9）：调用前必须过 RunBudget.plan_call——审计门①发现原实现
绕过预算后的修正；调用后 record 记账。

依赖注入：client 由构造方传入（测试用 Fake，生产用 OpenAICompatClient）。
节点不认识 mailbox/控制器——它只管「给消息历史，还更新后的历史」。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from ..run_budget import RunBudget


class ChatClient(Protocol):
    """最小客户端协议（OpenAICompatClient 与测试 Fake 均满足）。"""

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


def make_llm_node(
    client: ChatClient,
    *,
    system: str | None = None,
    budget: RunBudget | None = None,
) -> Callable[[dict], dict]:
    """构造 llm 节点。state 约定：进入时含 `messages`（user 消息已就位），
    退出时追加 `{"role": "assistant", "content": ...}` 并记录 `usage`。"""
    run_budget = budget or RunBudget()

    def llm_node(state: dict) -> dict:
        allowed, reason = run_budget.plan_call()
        if not allowed:
            updated = dict(state)
            updated["answer"] = f"[预算耗尽：{reason}]"
            updated["stop_reason"] = f"budget:{reason}"
            updated["budget"] = run_budget.snapshot()
            return updated

        messages = list(state.get("messages") or [])
        if system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": system}] + messages

        response = client.chat(messages)
        run_budget.record(getattr(response, "usage", None))
        answer = getattr(response, "content", "")
        usage = getattr(response, "usage", None) or {}

        updated = dict(state)
        updated["messages"] = messages + [{"role": "assistant", "content": answer}]
        updated["answer"] = answer
        updated["usage"] = dict(usage)
        updated["budget"] = run_budget.snapshot()
        return updated

    return llm_node
