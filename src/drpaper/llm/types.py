"""LLM 层共享类型：消息格式、工具定义与事件回调。

消息与工具定义统一采用 OpenAI 格式作为内部标准；其他协议（如 Anthropic）
在请求边界做翻译，上层模块一律感知不到差异。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
