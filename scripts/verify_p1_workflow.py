"""P1-3 验收：Workflow 文档化（真实 LLM）。

用法：uv run python scripts/verify_p1_workflow.py
三项检查：
1. 阶段文档随流程注入：起草指令触发调研/提纲阶段时，对应流程文档进入对话，且模型停在提纲确认节点；
2. 只改 Markdown 即改流程：把内置文档复制到临时目录、给「提纲与确认」阶段追加一条验收标准
   （展示提纲时回复以「【提纲待确认】」开头），不改任何 Python 代码，真实模型照做；
3. 系统提示词不再内嵌固定流程：流程细节只存在于 Markdown，常驻提示词只有阶段链索引。
场景 1/2 挂 stub 检索源（离线假文献），避免真实联网检索。
"""

from __future__ import annotations

import shutil
import re
import sys
from dataclasses import replace
from pathlib import Path

from verify_p0_routing import _StubProvider

from drpaper.agent.core import PaperAgent
from drpaper.agent.prompts import build_system_prompt
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import LLMClient
from drpaper.skills.manager import SkillManager
from drpaper.workflows import WorkflowManager
from drpaper.workflows import manager as workflows_manager

OVERRIDE_ROOT = Path(".output/verify_workflow_override")


class _QuietStubProvider(_StubProvider):
    """离线检索源：标题与摘要去掉 Stub/Placeholder 字样，避免模型当异常上报。"""

    def search(self, query: str, max_results: int) -> list:
        papers = super().search(query, max_results)
        frames = ["Recent advances in", "An empirical study of"]
        return [
            replace(
                paper,
                title=f"{frames[i % len(frames)]} {query}",
                abstract=(
                    f"This paper examines {query}, describing the method, "
                    "experimental setup and main findings in detail."
                ),
            )
            for i, paper in enumerate(papers)
        ]


def _calls(messages: list[dict]) -> list[str]:
    return [
        call["function"]["name"]
        for msg in messages
        for call in msg.get("tool_calls") or []
    ]


def _reply(messages: list[dict]) -> str:
    return next(
        (
            m["content"]
            for m in reversed(messages)
            if m.get("role") == "assistant" and m["content"]
        ),
        "",
    )


def _agent(llm: LLMClient, workflows_dir: Path | None = None) -> PaperAgent:
    agent = PaperAgent(
        llm=llm, output_dir=".output", style=load_profile(), user_workflows_dir=workflows_dir
    )
    agent._toolbox.provider = _QuietStubProvider()  # 离线假文献，避免真实联网检索
    return agent


def _run_drafting(agent: PaperAgent, max_rounds: int = 12) -> None:
    agent.messages.append(
        {"role": "user", "content": "帮我写一篇关于大语言模型幻觉缓解的论文，正文 8000 字左右"}
    )
    reply = agent.llm.chat(
        agent.messages, TOOL_SCHEMAS, agent._toolbox.handlers(), max_rounds=max_rounds
    )
    if reply:
        agent.messages.append({"role": "assistant", "content": reply})


def _override_outline_docs() -> None:
    """复制内置流程文档并只改 Markdown：给提纲阶段追加一条验收标准。"""
    if OVERRIDE_ROOT.exists():
        shutil.rmtree(OVERRIDE_ROOT)
    OVERRIDE_ROOT.mkdir(parents=True)
    builtin = (
        Path(workflows_manager.__file__).parent / "builtin" / "paper-drafting"
    )
    shutil.copytree(builtin, OVERRIDE_ROOT / "paper-drafting")
    criterion = "- 向用户展示提纲时，回复必须以「【提纲待确认】」开头"
    outline = OVERRIDE_ROOT / "paper-drafting" / "stages" / "03-outline.md"
    text = outline.read_text(encoding="utf-8")
    outline.write_text(text.replace("## 可用工具", f"{criterion}\n\n## 可用工具"), encoding="utf-8")


def main() -> int:
    config = load_config()
    llm = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
    )
    results: list[tuple[bool, str, str]] = []

    # 检查 1：阶段文档随流程注入，模型按文档停在提纲确认节点
    agent = _agent(llm)
    _run_drafting(agent)
    tool_texts = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    names = _calls(agent.messages)
    stages = sorted(
        set(re.findall(r"【(?:workflow：[\w-]+|当前阶段：[^】]+)】", "\n".join(tool_texts)))
    )
    ok = (
        "【当前阶段：文献调研】" in stages
        and "【当前阶段：提纲与确认】" in stages
        and "submit_section" not in names
        and "write_section" not in names
    )
    results.append(
        (
            ok,
            "阶段文档随工具结果注入，模型停在提纲确认节点",
            f"工具: {names or ['无']}；注入阶段: {stages}",
        )
    )

    # 检查 2：只改 Markdown 即改流程——给提纲阶段追加验收标准，模型照做
    _override_outline_docs()
    agent = _agent(llm, workflows_dir=OVERRIDE_ROOT)
    _run_drafting(agent)
    reply = _reply(agent.messages)
    criterion = "向用户展示提纲时，回复必须以「【提纲待确认】」开头"
    ok = "【提纲待确认】" in reply
    results.append(
        (
            ok,
            "只改 Markdown 追加验收标准，未改 Python 代码，模型照做",
            f"新增标准: {criterion}；回复: …{reply[:80]}",
        )
    )

    # 检查 3：常驻系统提示词不再内嵌固定流程，只有阶段链索引
    workflow_index = WorkflowManager().index_prompt()
    prompt = build_system_prompt(SkillManager().index_prompt(), workflow_index, 5000)
    ok = (
        "论文起草流程（起草任务严格按序执行）" not in prompt
        and "提纲与确认" in prompt
        and "导出交付" in prompt
    )
    results.append(
        (
            ok,
            "系统提示词只有阶段链索引，流程细节全部在 Markdown",
            f"提示词长度 {len(prompt)} 字符，含 workflow 索引: {'paper-drafting' in prompt}",
        )
    )

    passed = 0
    for ok, label, detail in results:
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        print(f"      {detail}\n")
    shutil.rmtree(OVERRIDE_ROOT, ignore_errors=True)
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
