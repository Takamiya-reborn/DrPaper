"""LLM 客户端：封装 OpenAI 兼容接口与 tool-calling 循环。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

Message = dict[str, Any]
Tool = dict[str, Any]
ToolHandler = Callable[[dict[str, Any]], str]


@dataclass
class ChatCallbacks:
    """chat 过程的事件回调，全部可选；由界面层实现渲染。"""

    on_text: Callable[[str], None] | None = None             # 流式正文 token
    on_first_token: Callable[[], None] | None = None         # 本轮首个正文 token 到达
    on_round_start: Callable[[], None] | None = None         # 每次发起模型请求前
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None  # (工具名, 参数)
    on_tool_end: Callable[[str, float], None] | None = None  # (工具名, 耗时秒)
    on_usage: Callable[[int, int, int], None] | None = None  # (输入, 输出, 合计) token


# 瞬时异常：指数退避重试
_TRANSIENT_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

_MAX_ATTEMPTS = 3      # 单次请求最大尝试次数
_EMPTY_REPLIES = 2     # 空回复最多重试次数


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

    def __post_init__(self) -> None:
        # 个别兼容服务不支持 stream_options，报错后降级并记住，会话内不再携带
        self._usage_degraded = False

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool],
        handlers: dict[str, ToolHandler],
        callbacks: ChatCallbacks | None = None,
        max_rounds: int = 40,
    ) -> str:
        """带工具调用的对话循环，返回最终助手回复文本。

        - 流式输出正文到 on_text 回调，工具执行经由 on_tool_start/end 通知
        - 每轮模型请求工具则执行后继续，直到给出纯文本回复
        - 工具轮次正文为空是预期行为（界面负责状态提示），不算空回复
        """
        empty_retries = 0
        for _ in range(max_rounds):
            if callbacks and callbacks.on_round_start:
                callbacks.on_round_start()
            content, tool_calls, finish = self._request(messages, tools, callbacks)

            # 截断回复整体丢弃：截断的工具调用参数进历史会导致部分服务 400
            if finish == "length":
                messages.append(
                    {
                        "role": "user",
                        "content": "上一条回复因输出长度上限被截断。"
                        "请把内容拆得更小（例如每次只提交一节），或精简后重新作答。",
                    }
                )
                continue

            if tool_calls:
                # 空 content 与 tool_calls 的组合部分服务会拒绝，规整为 None
                messages.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": [
                            {
                                "id": slot["id"],
                                "type": "function",
                                "function": {
                                    "name": slot["name"],
                                    "arguments": slot["arguments"],
                                },
                            }
                            for slot in tool_calls.values()
                        ],
                    }
                )
                for slot in tool_calls.values():
                    result = _run_handler(handlers, slot["name"], slot["arguments"], callbacks)
                    messages.append(
                        {"role": "tool", "tool_call_id": slot["id"], "content": result}
                    )
                continue

            # 最终回复为空：附加提示重试（提示留在历史中，防止模型再次空转）
            if not content.strip():
                if empty_retries < _EMPTY_REPLIES:
                    empty_retries += 1
                    messages.append(
                        {"role": "user", "content": "（上一条回复为空，请直接重新输出回复。）"}
                    )
                    continue
                return "（模型连续返回空回复，请重试或换一种说法。）"
            empty_retries = 0
            return content

        return (
            f"（已达单次任务轮次上限 {max_rounds} 轮，任务未完成。"
            "请总结当前进度，或发送「继续」接续。）"
        )

    def _request(
        self,
        messages: list[Message],
        tools: list[Tool],
        callbacks: ChatCallbacks | None,
    ) -> tuple[str, dict[int, dict[str, Any]], str]:
        """发起一次流式请求并消费，返回 (正文, 工具调用表, finish_reason)。"""
        for attempt in range(_MAX_ATTEMPTS):
            received = False
            first_token_fired = False
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            finish = ""
            try:
                stream = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools or None,
                    stream=True,
                    **self._usage_kwargs(),
                )
                for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage is not None and callbacks and callbacks.on_usage:
                        callbacks.on_usage(
                            usage.prompt_tokens, usage.completion_tokens, usage.total_tokens
                        )
                    if not chunk.choices:  # 部分兼容服务会发 choices 为空的心跳/统计块
                        continue
                    received = True
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish = choice.finish_reason
                    delta = choice.delta
                    if delta.content:
                        if not first_token_fired:
                            first_token_fired = True
                            if callbacks and callbacks.on_first_token:
                                callbacks.on_first_token()
                        content_parts.append(delta.content)
                        if callbacks and callbacks.on_text:
                            callbacks.on_text(delta.content)
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
            except BadRequestError as exc:
                # stream_options 不被支持：降级去掉该参数重试一次
                if "stream_options" in str(exc):
                    self._usage_degraded = True
                    continue
                raise
            except _TRANSIENT_ERRORS:
                # 正文已流式打印给用户时重试会重复输出，只能放弃
                if received or attempt == _MAX_ATTEMPTS - 1:
                    raise
                time.sleep(2**attempt)
                continue
            return "".join(content_parts), tool_calls, finish
        raise RuntimeError("请求重试次数耗尽")

    def _usage_kwargs(self) -> dict[str, Any]:
        """token 统计参数；服务不支持时降级为空。"""
        if self._usage_degraded:
            return {}
        return {"stream_options": {"include_usage": True}}


def _run_handler(
    handlers: dict[str, ToolHandler],
    name: str,
    arguments: str,
    callbacks: ChatCallbacks | None,
) -> str:
    """解析参数并执行工具，返回结果文本；同时触发工具执行事件。"""
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError as exc:
        return json.dumps(
            {"error": f"参数解析失败（可能被截断，请拆小后重试）: {exc}"}, ensure_ascii=False
        )
    if callbacks and callbacks.on_tool_start:
        callbacks.on_tool_start(name, args)
    start = time.perf_counter()
    handler = handlers.get(name)
    if handler is None:
        result = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    else:
        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001 - 工具异常回传给模型自行调整
            result = json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
    if callbacks and callbacks.on_tool_end:
        callbacks.on_tool_end(name, time.perf_counter() - start)
    return result
