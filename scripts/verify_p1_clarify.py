"""P1-1 验收：澄清优先 + 提纲确认硬门禁（真实 LLM）。

用法：uv run python scripts/verify_p1_clarify.py
三条场景：
1. 宽泛指令「帮我写篇论文」→ 先追问，不猜题目、不开写；
2. 指令带主题与字数 → 检索并提交提纲后停在确认节点，不直接写正文；
3. 用户确认后 → 先 confirm_outline 再 submit_section（门禁解锁顺序正确）。
场景 2/3 挂 stub 检索源（离线假文献），避免真实联网检索。
"""

from __future__ import annotations

import json
import sys

from verify_p0_routing import _StubProvider

from drpaper.agent.core import PaperAgent
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import LLMClient


def _calls(messages: list[dict]) -> list[tuple[str, dict]]:
    """按发生顺序收集全部 tool_calls。"""
    calls = []
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            try:
                args = json.loads(call["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            calls.append((call["function"]["name"], args))
    return calls


def _reply(messages: list[dict]) -> str:
    return next(
        (
            m["content"]
            for m in reversed(messages)
            if m.get("role") == "assistant" and m.get("content")
        ),
        "",
    )


def _run(agent: PaperAgent, text: str, handlers: dict, max_rounds: int) -> str:
    """追加一条用户输入并跑指定轮数的 agent loop（PaperAgent.chat 不暴露轮数）。

    与 PaperAgent.chat 一致：最终回复由调用方追加进历史。
    """
    agent.messages.append({"role": "user", "content": text})
    reply = agent.llm.chat(agent.messages, TOOL_SCHEMAS, handlers, max_rounds=max_rounds)
    if reply:
        agent.messages.append({"role": "assistant", "content": reply})
    return reply


def main() -> int:
    config = load_config()
    llm = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
    )
    results: list[tuple[bool, str, str]] = []

    # 场景 1：宽泛指令 → 先追问（只给 load_skill，检索类工具不可用，迫使走文本）
    agent = PaperAgent(llm=llm, output_dir=".output", style=load_profile())
    reply = ""
    for attempt in range(3):  # 个别轮次模型会返回空文本，重试
        reply = _run(
            agent,
            "帮我写篇论文" if attempt == 0 else "（上一条回复为空，请直接向用户提问）",
            {"load_skill": agent._toolbox._load_skill},
            max_rounds=4,
        )
        if reply.strip():
            break
    calls = _calls(agent.messages)
    ok = (
        not any(name in {"begin_draft", "submit_section"} for name, _ in calls)
        and "？" in reply
    )
    results.append(
        (
            ok,
            "宽泛指令 → 先追问研究方向/要求，不开写",
            f"工具: {[n for n, _ in calls] or ['无']}；回复: …{reply[-80:]}",
        )
    )

    # 场景 2：带主题与字数 → 提交提纲后停在确认节点
    agent = PaperAgent(llm=llm, output_dir=".output", style=load_profile())
    agent._toolbox.provider = _StubProvider()  # 离线假文献，避免真实联网检索
    _run(
        agent,
        "帮我写一篇关于大语言模型幻觉缓解的论文，正文 8000 字左右",
        agent._toolbox.handlers(),
        max_rounds=12,
    )
    calls = _calls(agent.messages)
    names = [n for n, _ in calls]
    ok = (
        any(n == "set_target_chars" and a.get("target_chars") == 8000 for n, a in calls)
        and "begin_draft" in names
        and "submit_section" not in names
    )
    results.append(
        (
            ok,
            "明确指令 → 提纲提交后停在确认节点，未动笔",
            f"工具: {names or ['无']}；回复: …{_reply(agent.messages)[-80:]}",
        )
    )

    # 场景 3：用户确认 → confirm_outline 先于 submit_section
    _run(agent, "可以，开始写", agent._toolbox.handlers(), max_rounds=12)
    start = next(
        i
        for i, m in enumerate(agent.messages)
        if m.get("role") == "user" and "可以" in m["content"]
    )
    names = [n for n, _ in _calls(agent.messages[start:])]
    confirm_at = names.index("confirm_outline") if "confirm_outline" in names else -1
    submit_at = names.index("submit_section") if "submit_section" in names else -1
    ok = confirm_at != -1 and submit_at != -1 and confirm_at < submit_at
    results.append(
        (
            ok,
            "用户确认 → 先 confirm_outline 再 submit_section",
            f"工具: {names or ['无']}",
        )
    )

    passed = 0
    for ok, label, detail in results:
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        print(f"      {detail}\n")
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
