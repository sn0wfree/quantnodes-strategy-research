"""llm 节点（阶段 1a）—— 一次 LLM 调用，把回答追加进消息历史。

依赖注入：client 由构造方传入（测试用 Fake，生产用 OpenAICompatClient）。
节点不认识 mailbox/控制器——它只管「给消息历史，还更新后的历史」。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class ChatClient(Protocol):
    """最小客户端协议（OpenAICompatClient 与测试 Fake 均满足）。"""

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


def make_llm_node(client: ChatClient, *, system: str | None = None) -> Callable[[dict], dict]:
    """构造 llm 节点。state 约定：进入时含 `messages`（user 消息已就位），
    退出时追加 `{"role": "assistant", "content": ...}` 并记录 `usage`。"""

    def llm_node(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        if system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": system}] + messages

        response = client.chat(messages)
        answer = getattr(response, "content", "")
        usage = getattr(response, "usage", None) or {}

        updated = dict(state)
        updated["messages"] = messages + [{"role": "assistant", "content": answer}]
        updated["answer"] = answer
        updated["usage"] = dict(usage)
        return updated

    return llm_node
