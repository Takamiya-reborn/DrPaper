"""子代理公共骨架：独立上下文的完整 agent loop，只向主循环返回结构化摘要。

主循环经各子代理的 run() 派发任务：任务描述进子代理自己的消息历史，
检索结果、正文草稿等中间过程全部留在子代理上下文里；子代理用 submit
类工具交回结构化产物，由处理器捕获后作为 run() 的返回值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from drpaper.llm.client import LLMClient
from drpaper.llm.types import ChatCallbacks, ContextCompactor, Tool, ToolHandler

__all__ = ["Subagent", "error", "result"]


def result(**fields: Any) -> str:
    """装配给模型看的 JSON 结果。"""
    return json.dumps(fields, ensure_ascii=False)


def error(message: str, **fields: Any) -> str:
    return result(error=message, **fields)


@dataclass
class Subagent:
    """一个子代理 = 独立消息历史 + 独立工具集 + 独立轮次上限。"""

    llm: LLMClient
    system: str
    tools: list[Tool]
    handlers: dict[str, ToolHandler]
    max_rounds: int = 12
    compactor: ContextCompactor | None = None  # 上下文压缩器（与主循环共用）

    def run(self, task: str, callbacks: ChatCallbacks | None = None) -> str:
        """在子代理自己的上下文里执行完整 agent loop，返回最终文本回复。

        结构化产物由 submit 类工具处理器捕获（存入子代理实例状态），
        不经最终回复携带——与主循环的工具约定一致。
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": task},
        ]
        # 子代理正文不流式输出给用户（中间过程不进主对话），只透传工具与轮次事件
        forwarded = ChatCallbacks()
        if callbacks is not None:
            forwarded.on_round_start = callbacks.on_round_start
            forwarded.on_tool_start = callbacks.on_tool_start
            forwarded.on_tool_end = callbacks.on_tool_end
            forwarded.on_usage = callbacks.on_usage
        return self.llm.chat(
            messages,
            self.tools,
            self.handlers,
            callbacks=forwarded,
            max_rounds=self.max_rounds,
            compactor=self.compactor,
        )
