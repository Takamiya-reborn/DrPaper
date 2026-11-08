"""LLM 客户端：封装多 provider 对话接口与 tool-calling 循环。

内部历史消息统一为 OpenAI 格式；Anthropic 由 llm.anthropic_backend 在请求
边界做翻译，chat 循环、重试与空回复处理对上层完全一致。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError as OpenAIBadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from drpaper.llm import anthropic_backend
from drpaper.llm.types import ChatCallbacks, ContextCompactor, Message, Tool, ToolHandler

__all__ = ["ChatCallbacks", "LLMClient", "Message", "Tool", "ToolHandler"]

# 瞬时异常：指数退避重试（两套 SDK 的异常合并，请求路径只会抛出其一）
_TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    *anthropic_backend.TRANSIENT_ERRORS,
)
_BAD_REQUEST_ERRORS = (OpenAIBadRequestError, anthropic_backend.BadRequestError)

_MAX_ATTEMPTS = 3      # 单次请求最大尝试次数
_EMPTY_REPLIES = 2     # 空回复最多重试次数
_LOOP_BREAKS = 3       # 同一工具重复相同错误达此次数即熔断本轮剩余调用
_MAX_BREAKS = 2        # 熔断后仍重蹈覆辙的次数上限，超过直接终止本轮任务
_LOOP_STUB = json.dumps(
    {"error": "熔断生效，本轮剩余调用未执行；请先按上文提示纠正后再继续"},
    ensure_ascii=False,
)


@dataclass
class _StreamState:
    """单次流式请求进度：正文是否已开始输出（决定异常后能否安全重试）。"""

    received: bool = False


@dataclass
class LLMClient:
    """多 provider 客户端，负责底层对话与工具执行。"""

    api_key: str
    base_url: str
    model: str
    provider: str = "openai"  # openai（含兼容接口）| anthropic

    @cached_property
    def _client(self) -> OpenAI | Any:
        """复用同一客户端，避免每轮对话重建 HTTP 连接池。"""
        if self.provider == "anthropic":
            return anthropic_backend.create_client(self.api_key, self.base_url)
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
        compactor: ContextCompactor | None = None,
    ) -> str:
        """带工具调用的对话循环，返回最终助手回复文本。

        - 流式输出正文到 on_text 回调，工具执行经由 on_tool_start/end 通知
        - 每轮模型请求工具则执行后继续，直到给出纯文本回复
        - 工具轮次正文为空是预期行为（界面负责状态提示），不算空回复
        - 同一工具重复相同错误达 _LOOP_BREAKS 次即熔断，重蹈覆辙超限则终止任务
        - 每轮请求前经 compactor 检查上下文占用，超阈值时先把旧历史折叠为摘要
        """
        empty_retries = 0
        error_counts: dict[tuple[str, str], int] = {}  # (工具名, 错误结果) → 出现次数
        breaks = 0
        # 最近一次请求的实测输入 token 数，供 compactor 判断窗口占用
        last_prompt = 0
        inner = callbacks or ChatCallbacks()

        def _on_usage(prompt: int, completion: int, total: int) -> None:
            nonlocal last_prompt
            last_prompt = prompt
            if inner.on_usage:
                inner.on_usage(prompt, completion, total)

        effective = ChatCallbacks(
            on_text=inner.on_text,
            on_first_token=inner.on_first_token,
            on_round_start=inner.on_round_start,
            on_tool_start=inner.on_tool_start,
            on_tool_end=inner.on_tool_end,
            on_usage=_on_usage,
        )
        for _ in range(max_rounds):
            if compactor is not None:
                compactor.before_round(messages, last_prompt)
            if effective.on_round_start:
                effective.on_round_start()
            content, tool_calls, finish = self._request(messages, tools, effective)

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
                # 错误熔断：模型对同一错误反复重试会白烧轮次并污染上下文，
                # 达阈值后停止执行本轮剩余调用并注入纠偏提示
                tripped = False
                for slot in tool_calls.values():
                    if tripped:
                        result = _LOOP_STUB
                    else:
                        result = _run_handler(
                            handlers, slot["name"], slot["arguments"], effective
                        )
                    messages.append(
                        {"role": "tool", "tool_call_id": slot["id"], "content": result}
                    )
                    if result.startswith('{"error"'):
                        key = (slot["name"], result)
                        error_counts[key] = error_counts.get(key, 0) + 1
                        tripped = tripped or error_counts[key] >= _LOOP_BREAKS
                    else:
                        # 工具恢复正常即清其错误计数，避免误熔断后续合法重试
                        for k in [k for k in error_counts if k[0] == slot["name"]]:
                            del error_counts[k]
                if tripped:
                    breaks += 1
                    if breaks > _MAX_BREAKS:
                        return (
                            "（工具调用陷入重复错误循环，已中止本轮任务。"
                            "请总结当前进度与卡点，或换一种做法重试。）"
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": "（同一工具连续返回相同错误，说明你在重复无效调用。"
                            "停止重试，先按最近一次错误提示改做正确操作；"
                            "仍无法解决时向用户说明情况并结束本轮。）",
                        }
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

    def complete(
        self,
        messages: list[Message],
        callbacks: ChatCallbacks | None = None,
    ) -> str:
        """无工具的单轮补全，返回正文；持续空回复时返回空串。

        内部调用（上下文压缩摘要等）专用：与 chat 共用请求层（流式消费、
        瞬时错误重试），只是不携带工具定义，也不会改动传入的消息列表。
        """
        for attempt in range(2):
            content, _tool_calls, _finish = self._request(messages, [], callbacks)
            if content.strip():
                return content
            if attempt == 0:
                time.sleep(1)
        return ""

    def _request(
        self,
        messages: list[Message],
        tools: list[Tool],
        callbacks: ChatCallbacks | None,
    ) -> tuple[str, dict[int, dict[str, Any]], str]:
        """发起一次流式请求并消费，返回 (正文, 工具调用表, finish_reason)。"""
        for attempt in range(_MAX_ATTEMPTS):
            state = _StreamState()
            try:
                content, tool_calls, finish = self._consume(messages, tools, callbacks, state)
            except _BAD_REQUEST_ERRORS as exc:
                # stream_options 不被支持：仅 openai 路径降级去掉该参数重试一次
                if self.provider == "openai" and "stream_options" in str(exc):
                    self._usage_degraded = True
                    continue
                raise
            except _TRANSIENT_ERRORS:
                # 正文已流式打印给用户时重试会重复输出，只能放弃
                if state.received or attempt == _MAX_ATTEMPTS - 1:
                    raise
                time.sleep(2**attempt)
                continue
            return content, tool_calls, finish
        raise RuntimeError("请求重试次数耗尽")

    def _consume(
        self,
        messages: list[Message],
        tools: list[Tool],
        callbacks: ChatCallbacks | None,
        state: _StreamState,
    ) -> tuple[str, dict[int, dict[str, Any]], str]:
        """按 provider 分发到对应的流式消费实现。"""
        if self.provider == "anthropic":
            return anthropic_backend.consume_stream(
                self._client, self.model, messages, tools, callbacks, state
            )
        return self._consume_openai(messages, tools, callbacks, state)

    def _consume_openai(
        self,
        messages: list[Message],
        tools: list[Tool],
        callbacks: ChatCallbacks | None,
        state: _StreamState,
    ) -> tuple[str, dict[int, dict[str, Any]], str]:
        """openai（含兼容接口）路径：发起一次流式请求并消费。"""
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish = ""
        first_token_fired = False
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
            state.received = True
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
        return "".join(content_parts), tool_calls, finish

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
