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


def chunk(content=None, tool_calls=None, finish=None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        usage=None, choices=[SimpleNamespace(finish_reason=finish, delta=delta)]
    )


class FakeCompletions:
    """每次 create 消耗脚本中的一轮；只剩一轮时重复返回（测轮次上限用）。"""

    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self.rounds = rounds

    def create(self, **_kwargs):
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
