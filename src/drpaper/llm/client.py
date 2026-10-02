"""LLM 客户端：封装 OpenAI 兼容接口与 tool-calling 循环。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from openai import OpenAI

Message = dict[str, Any]
Tool = dict[str, Any]
ToolHandler = Callable[[dict[str, Any]], str]


@dataclass
class LLMClient:
    """OpenAI 兼容客户端，负责底层对话与工具执行。"""

    api_key: str
    base_url: str
    model: str

    @cached_property
    def _client(self) -> OpenAI:
        """复用同一客户端，避免每轮对话重建 HTTP 连接池。"""
        return OpenAI(api_key=self.api_key, base_url=self.base_url or None)

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool],
        handlers: dict[str, ToolHandler],
        on_text: Callable[[str], None] | None = None,
        max_rounds: int = 16,
    ) -> str:
        """带工具调用的对话循环，返回最终助手回复文本。

        - 流式输出正文到 on_text 回调
        - 每轮模型请求工具则执行后继续，直到给出纯文本回复
        """
        client = self._client()
        for _ in range(max_rounds):
            stream = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools or None,
                stream=True,
            )
            assistant: Message = {"role": "assistant", "content": ""}
            tool_calls: dict[int, dict[str, Any]] = {}

            for chunk in stream:
                if not chunk.choices:  # 部分兼容服务会发 choices 为空的心跳/统计块
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    assistant["content"] += delta.content
                    if on_text:
                        on_text(delta.content)
                for call in delta.tool_calls or []:
                    slot = tool_calls.setdefault(
                        call.index, {"id": call.id or "", "name": "", "arguments": ""}
                    )
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name:
                        slot["name"] += call.function.name
                    if call.function and call.function.arguments:
                        slot["arguments"] += call.function.arguments

            if not tool_calls:
                return str(assistant["content"])

            assistant["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for slot in tool_calls.values()
            ]
            messages.append(assistant)

            for slot in tool_calls.values():
                result = _run_handler(handlers, slot["name"], slot["arguments"])
                messages.append(
                    {"role": "tool", "tool_call_id": slot["id"], "content": result}
                )

        return "（达到工具调用轮次上限，已停止。）"


def _run_handler(handlers: dict[str, ToolHandler], name: str, arguments: str) -> str:
    handler = handlers.get(name)
    if handler is None:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"参数解析失败: {exc}"}, ensure_ascii=False)
    try:
        return handler(args)
    except Exception as exc:  # noqa: BLE001 - 工具异常回传给模型自行调整
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
