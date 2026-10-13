"""Anthropic 原生协议适配：消息翻译与流式消费。

内部历史统一为 OpenAI 消息格式（见 llm.types），本模块只在请求边界做转换：

- system 消息 → 顶层 system 参数
- assistant.tool_calls → tool_use 内容块；role=tool → tool_result 内容块，
  连续的 tool 消息合并进同一条 user 消息（Anthropic 要求 tool_result 紧随
  对应的 tool_use）；相邻 user 消息合并（该协议不允许连续同角色消息）
- 流式事件（message_start / content_block_delta / message_delta）映射为与
  openai 路径相同的 (正文, 工具调用表, finish_reason) 三元组
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import (
    Anthropic,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from drpaper.llm.types import ChatCallbacks, Message, Tool

TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

_MAX_OUTPUT_TOKENS = 16384  # max_tokens 为 Anthropic 必填参数，取当前全系模型都支持的量级


def create_client(api_key: str, base_url: str) -> Anthropic:
    """创建 Anthropic 客户端；base_url 仅在走代理/网关时非空。"""
    return Anthropic(api_key=api_key, base_url=base_url or None)


def consume_stream(
    client: Anthropic,
    model: str,
    messages: list[Message],
    tools: list[Tool],
    callbacks: ChatCallbacks | None,
    state: Any | None = None,
) -> tuple[str, dict[int, dict[str, Any]], str]:
    """发起一次流式请求并消费，返回 (正文, 工具调用表, finish_reason)。

    state 为 client 侧传入的 _StreamState：正文开始输出后置 received=True，
    异常时据此判断能否安全重试（避免重复打印）。
    """
    system, converted = convert_messages(messages)
    stream = client.messages.create(
        model=model,
        max_tokens=_MAX_OUTPUT_TOKENS,
        system=system,
        messages=converted,
        tools=convert_tools(tools) or None,
        stream=True,
    )
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish = ""
    input_tokens = 0
    first_token_fired = False
    for event in stream:
        if event.type == "message_start":
            input_tokens = event.message.usage.input_tokens
        elif event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                tool_calls[event.index] = {
                    "id": event.content_block.id,
                    "name": event.content_block.name,
                    "arguments": "",
                }
        elif event.type == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta":
                if not first_token_fired:
                    first_token_fired = True
                    if state is not None:
                        state.received = True
                    if callbacks and callbacks.on_first_token:
                        callbacks.on_first_token()
                content_parts.append(delta.text)
                if callbacks and callbacks.on_text:
                    callbacks.on_text(delta.text)
            elif delta.type == "input_json_delta" and event.index in tool_calls:
                tool_calls[event.index]["arguments"] += delta.partial_json
        elif event.type == "message_delta":
            # stop_reason 与 openai 路径对齐：截断 → length，其余由 tool_calls 是否非空决定
            if event.delta.stop_reason:
                finish = "length" if event.delta.stop_reason == "max_tokens" else "stop"
            # 用量每次请求只上报一次（界面层按请求累加）
            if callbacks and callbacks.on_usage:
                output = event.usage.output_tokens
                callbacks.on_usage(input_tokens, output, input_tokens + output)
    return "".join(content_parts), tool_calls, finish


def convert_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI 消息 → (system 文本, Anthropic 消息列表)；相邻 user 消息合并。"""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    def append_user(block: dict[str, Any]) -> None:
        """追加 user 内容块；前一条也是 user 时并入其中（协议禁止连续同角色）。"""
        if converted and converted[-1]["role"] == "user":
            converted[-1]["content"].append(block)
        else:
            converted.append({"role": "user", "content": [block]})

    for msg in messages:
        role = msg["role"]
        if role == "system":
            system_parts.append(msg["content"])
        elif role == "user":
            append_user({"type": "text", "text": msg["content"]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": _parse_arguments(call["function"]["arguments"]),
                    }
                )
            if not blocks:  # 防御：空 assistant 消息该协议不允许
                blocks = [{"type": "text", "text": ""}]
            converted.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            append_user(
                {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
    return "\n\n".join(system_parts) or None, converted


def convert_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """OpenAI function schema → Anthropic input_schema。"""
    return [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
            "input_schema": tool["function"].get("parameters")
            or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


def _parse_arguments(arguments: str) -> dict[str, Any]:
    """解析工具调用参数；截断或非法 JSON 降级为空对象，由工具层报错。"""
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}
