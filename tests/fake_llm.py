"""测试共用：假流式客户端与假 Agent 装配（离线驱动真实 chat 循环）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from drpaper.agent.core import PaperAgent
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import LLMClient


def tool_call(name: str, arguments: dict, index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=f"call_{index}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def chunk(content=None, tool_calls=None, finish=None, usage=None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        usage=usage, choices=[SimpleNamespace(finish_reason=finish, delta=delta)]
    )


def usage(prompt_tokens: int, completion_tokens: int = 10) -> SimpleNamespace:
    """构造流式响应里的用量块（openai 格式），供压缩触发测试用。"""
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


class FakeCompletions:
    """每次 create 消耗脚本中的一轮；只剩一轮时重复返回（测轮次上限用）。

    requests 记录每次请求收到的消息历史（主循环与子代理按调用顺序混排），
    供测试核对子代理的独立上下文与系统提示词。
    """

    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self.rounds = rounds
        self.requests: list[list[dict]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs["messages"])
        if len(self.rounds) > 1:
            return iter(self.rounds.pop(0))
        return iter(self.rounds[0])


class FakeStreamClient:
    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(rounds))


def agent_with(rounds: list[list[SimpleNamespace]], tmp_path) -> PaperAgent:
    agent = PaperAgent(
        llm=LLMClient(api_key="test", base_url="", model="test-model"),
        output_dir=str(tmp_path),
        style=load_profile(),
    )
    agent.llm._client = FakeStreamClient(rounds)
    return agent


def tool_results(messages: list[dict]) -> list[dict]:
    """解析主循环 tool 消息里的 JSON 结果。

    进入新 workflow 阶段时流程文档附在 JSON 之后（\n\n---\n\n 分隔），截断后解析。
    """
    results = []
    for m in messages:
        if m.get("role") != "tool" or not m["content"].lstrip().startswith("{"):
            continue
        results.append(json.loads(m["content"].split("\n\n---\n\n", 1)[0]))
    return results
