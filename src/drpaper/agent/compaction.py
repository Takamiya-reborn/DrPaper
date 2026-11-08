"""上下文压缩（compaction）：长任务中把超出阈值的历史折叠为摘要。

agent loop 每轮请求前调用 Compactor.before_round：实测（或估算）输入 token
超过阈值（窗口 × threshold，如 70%）时，把保留区之外的历史经一次摘要调用
折叠为单条 user 消息，就地替换原历史。摘要之外还随附一份结构化状态快照
（state_snapshot）：提纲与确认状态、任务清单、文献编号对照等权威状态不依赖
模型转述，从机制上保证压缩后不遗忘已确认的提纲与写作要求。

压缩器对消息历史无记忆（触发依据全部来自调用方传入的实测用量或当次估算），
主循环与各子代理可安全共用同一个实例。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from drpaper.llm.client import ChatCallbacks, LLMClient
from drpaper.llm.types import Message

# 默认上下文窗口（tokens）；模型窗口不同时用 DRPAPER_CONTEXT_WINDOW 覆盖
DEFAULT_WINDOW_TOKENS = 128_000
_ENV_WINDOW = "DRPAPER_CONTEXT_WINDOW"

# 折叠边界之后至少保留的最近消息条数：给模型留足当前任务的直接上下文
KEEP_RECENT = 6

# 送入摘要调用的单条消息截断上限：摘要调用本身也要塞得进窗口
_TRANSCRIPT_CAP = 12_000

_FOLDED_HEADER = (
    "（以下是此前对话的压缩摘要，更早的详细历史已被折叠；"
    "请基于摘要与之后的最近消息继续当前任务，禁止重做已完成步骤。）"
)

_SUMMARY_SYSTEM = """你是对话压缩器：把一段多轮 Agent 对话历史折叠为后续工作可完全依赖的摘要。
摘要必须按以下顺序用条目式中文保留（有则保留，无则跳过该条）：
1. 已确定的关键决策：论文题目、已提交/已确认的提纲（节名与字数预算）、目标字数、方法与结构选型；
2. 用户明确的要求：尽量保留原话要点，禁止改变含义或遗漏；
3. 未完成事项：当前进行到哪个步骤、刚发生的工具调用与结果、下一步要做什么；
4. 关键工具结果与约定：文献编号与标题对照、已提交各节的内容摘要、数据口径、待补充占位。
只输出摘要本身，禁止评论压缩行为或向用户喊话。"""


def context_window() -> int:
    """模型上下文窗口（tokens）：环境变量 DRPAPER_CONTEXT_WINDOW 可覆盖默认值。"""
    raw = os.getenv(_ENV_WINDOW, "").strip()
    if not raw:
        return DEFAULT_WINDOW_TOKENS
    try:
        return max(int(raw), 1000)
    except ValueError:
        return DEFAULT_WINDOW_TOKENS


def estimate_tokens(messages: list[Message]) -> int:
    """无实测用量时的粗估：中英混排按约 2 字符 ≈ 1 token，宁可高估早压不溢出。"""
    chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            chars += len(content)
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            chars += len(str(fn.get("arguments", "")))
    return chars // 2


def _clip(text: str, cap: int = _TRANSCRIPT_CAP) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    return f"{text[:half]}\n…（中间省略 {len(text) - cap} 字符）…\n{text[-half:]}"


@dataclass
class Compactor:
    """上下文压缩器：超阈值时把旧历史折叠为「摘要 + 权威状态快照」。"""

    llm: LLMClient
    window_tokens: int = DEFAULT_WINDOW_TOKENS
    threshold: float = 0.7  # 窗口占用超过该比例即触发压缩
    keep_recent: int = KEEP_RECENT
    # 压缩时随摘要注入的权威状态快照（ToolBox 状态块，不依赖模型转述）
    state_snapshot: Callable[[], str] | None = None
    # 摘要调用的用量回调（计入界面统计）；PaperAgent.chat 每轮刷新
    usage_cb: Callable[[int, int, int], None] | None = None
    compactions: int = 0  # 累计压缩次数（观测用）

    def trigger_tokens(self) -> int:
        return int(self.window_tokens * self.threshold)

    def before_round(self, messages: list[Message], prompt_tokens: int) -> bool:
        """agent loop 每轮请求前调用；超过阈值就地折叠历史，返回是否压缩。"""
        tokens = prompt_tokens if prompt_tokens > 0 else estimate_tokens(messages)
        if tokens < self.trigger_tokens():
            return False
        return self.compact(messages)

    def compact(self, messages: list[Message]) -> bool:
        """把 messages[1:boundary] 折叠为单条摘要消息（就地替换），返回是否折叠。"""
        boundary = self._boundary(messages)
        if boundary <= 1:
            return False  # 没有可安全折叠的历史
        summary = self._summarize(messages[1:boundary])
        if not summary:
            return False  # 摘要调用失败：本轮放弃压缩，下轮请求前重试
        content = f"{_FOLDED_HEADER}\n\n{summary}"
        snapshot = self.state_snapshot() if self.state_snapshot else ""
        if snapshot:
            content += (
                "\n\n## 权威状态（压缩前的结构化快照，以此为准；"
                "与摘要冲突时忽略摘要）\n\n"
                + snapshot
            )
        messages[1:boundary] = [{"role": "user", "content": content}]
        self.compactions += 1
        return True

    def _boundary(self, messages: list[Message]) -> int:
        """折叠边界：messages[1:boundary] 被折叠，boundary 起原样保留。

        边界不得落在 tool 消息上（会把 assistant.tool_calls 与其结果拆散，
        留下悬空的 tool_result）；历史不足 keep_recent 条可折叠时返回 0。
        """
        i = max(len(messages) - self.keep_recent, 1)
        while i > 1 and messages[i].get("role") == "tool":
            i -= 1
        return i if i > 1 else 0

    def _summarize(self, segment: list[Message]) -> str:
        """调用一次无工具补全生成摘要；失败返回空串（放弃本轮压缩）。"""
        payload = "\n\n".join(self._transcript(msg) for msg in segment)
        summary_messages: list[Message] = [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": f"请把以下对话历史折叠为摘要：\n\n{payload}"},
        ]
        callbacks = (
            ChatCallbacks(on_usage=self.usage_cb) if self.usage_cb else None
        )
        try:
            return self.llm.complete(summary_messages, callbacks=callbacks).strip()
        except Exception:  # noqa: BLE001 - 摘要失败不阻塞任务，下轮请求前重试
            return ""

    def _transcript(self, msg: Message) -> str:
        """单条消息的转写：保留角色、工具调用与结果，超长内容掐头去尾。"""
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            calls = "；".join(
                f"{call['function']['name']}({_clip(str(call['function'].get('arguments', '')), 2000)})"
                for call in msg["tool_calls"]
            )
            text = msg.get("content") or ""
            head = f"[assistant 正文] {_clip(str(text))}\n" if text else ""
            return f"{head}[assistant 调用工具] {calls}"
        content = msg.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        label = {
            "user": "[用户]",
            "assistant": "[assistant]",
            "tool": "[工具结果]",
            "system": "[system]",
        }.get(role, f"[{role}]")
        return f"{label} {_clip(text)}"


__all__ = ["Compactor", "DEFAULT_WINDOW_TOKENS", "context_window", "estimate_tokens"]
