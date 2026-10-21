"""P0-1 验收：口语化中文指令经真实 LLM 路由到正确工具。

用法：uv run python scripts/verify_p0_routing.py
默认单轮取首轮 tool_calls 判断路由，不实际执行工具（handlers 置空）；
标注多轮的条目挂真实工具与 stub 检索源，验证 begin_draft 前的目标字数设定。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from drpaper.agent.core import PaperAgent
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.literature.base import Paper
from drpaper.llm.client import LLMClient

# (指令, 检查函数, 说明, 最大轮数)；calls: list[(工具名, 参数字典)]
UTTERANCES: list[tuple[str, Callable[[list], bool], str, int]] = [
    (
        "帮我写一篇关于大语言模型幻觉缓解的论文，正文 8000 字左右",
        lambda calls: any(
            n == "set_target_chars" and a.get("target_chars") == 8000 for n, a in calls
        ),
        "起草指令 → begin_draft 前设定目标字数 8000（多轮，真实工具）",
        6,
    ),
    (
        "来篇 1.5 万字的稿子，主题是图神经网络在推荐系统里的应用",
        lambda calls: any(
            n == "set_target_chars" and a.get("target_chars") == 15000 for n, a in calls
        ),
        "「1.5 万」换算 → set_target_chars 15000",
        1,
    ),
    (
        "这个课题有人做过吗：基于知识图谱的推荐系统",
        lambda calls: any(
            n in {"search_literature", "load_skill"}
            and (n == "load_skill" or a.get("register") is False)
            for n, a in calls
        ),
        "查新 → 咨询检索 / novelty-check skill",
        1,
    ),
    (
        "遥感图像分割还有啥方向能做？推几篇能接续的论文",
        lambda calls: any(
            (n == "search_literature" and a.get("register") is False)
            or (n == "load_skill" and "research" in str(a.get("name", "")))
            for n, a in calls
        ),
        "求推荐 → 咨询检索或 research-guidance skill",
        1,
    ),
    (
        "帮我把这段摘要润色一下：本文提出了一种新的方法，效果很好",
        lambda calls: any(
            n == "load_skill" and "polish" in str(a.get("name", "")) for n, a in calls
        ),
        "润色 → academic-polish skill",
        1,
    ),
    (
        "字数改成 6000",
        lambda calls: any(
            n == "set_target_chars" and a.get("target_chars") == 6000 for n, a in calls
        ),
        "改字数 → set_target_chars 6000",
        1,
    ),
    (
        "导出 word 吧，别带批注",
        lambda calls: any(
            n == "export_docx" and a.get("include_guides") is False for n, a in calls
        ),
        "无批注导出 → export_docx(include_guides=false)",
        1,
    ),
    (
        "对比表里 SST-2 的 Accuracy 帮我估一个区间",
        lambda calls: (
            any(
                n == "estimate_metric" and "SST-2" in str(a.get("dataset", ""))
                for n, a in calls
            )
            or any(n == "search_literature" for n, _ in calls)
        ),
        "指标估计 → estimate_metric（空库时先检索补证据）",
        1,
    ),
    (
        "第3节太短了，给我扩到 1200 字",
        lambda calls: all(n != "set_target_chars" for n, _ in calls),
        "局部篇幅不得改全文目标（反例）",
        1,
    ),
    (
        "深度学习在教育领域还能做点啥",
        lambda calls: any(
            n in {"search_literature", "load_skill"}
            and (n == "load_skill" or a.get("register") is False)
            for n, a in calls
        ),
        "问方向 → 咨询检索 / research-guidance skill",
        1,
    ),
]


class _StubProvider:
    """离线检索源：返回两条固定假文献，让起草流程能推进到 begin_draft。"""

    name = "stub"
    warnings: list[str] = []

    def search(self, query: str, max_results: int) -> list[Paper]:
        return [
            Paper(
                uid=f"stub:{hash((query, i)) & 0xFFFF:04x}",
                title=f"Stub paper {i} on {query}",
                authors=["Author A"],
                year=2024,
                abstract=f"Placeholder abstract about {query}.",
                url="https://example.com",
            )
            for i in range(min(2, max_results))
        ]


def _collect_calls(
    agent: PaperAgent, text: str, max_rounds: int
) -> list[tuple[str, dict]]:
    """跑完指定轮数，收集全部 tool_calls；单轮时 handlers 置空不执行工具。"""
    agent.messages.append({"role": "user", "content": text})
    handlers = agent._toolbox.handlers() if max_rounds > 1 else {}
    agent.llm.chat(agent.messages, TOOL_SCHEMAS, handlers, max_rounds=max_rounds)
    calls = []
    for msg in agent.messages:
        for call in msg.get("tool_calls") or []:
            try:
                args = json.loads(call["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            calls.append((call["function"]["name"], args))
    return calls


def main() -> int:
    config = load_config()
    llm = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
    )
    passed = 0
    for text, check, label, max_rounds in UTTERANCES:
        agent = PaperAgent(llm=llm, output_dir=".output", style=load_profile())
        if max_rounds > 1:
            agent._toolbox.provider = _StubProvider()  # 离线假文献，避免真实联网检索
        calls = _collect_calls(agent, text, max_rounds)
        ok = check(calls)
        passed += ok
        names = [name for name, _ in calls] or ["（无工具调用）"]
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        print(f"      输入: {text}")
        print(f"      路由: {names}\n")
    print(f"通过 {passed}/{len(UTTERANCES)}")
    return 0 if passed == len(UTTERANCES) else 1


if __name__ == "__main__":
    sys.exit(main())
