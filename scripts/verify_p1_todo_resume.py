"""P1-4 验收：任务清单持久化与断点续跑（真实 LLM）。

用法：uv run python scripts/verify_p1_todo_resume.py
四项检查：
1. 清单持久化：起草过程用 update_todo 建立清单并随步更新，状态即时落盘 session.json；
2. 模拟中断：第一个会话只完成调研+提纲+第 1 节即放弃（等同进程被杀）；
3. 重启恢复：全新 PaperAgent 读同一 output_dir，草稿/文献库/清单恢复，
   系统提示词注入断点恢复块，已确认的提纲不再重复请求确认；
4. 接续不重做：说「继续」后直接写剩余节（不重新 begin_draft、不重写第 1 节），
   走完 finish_draft 并成功导出 docx。
检索挂 stub 源（离线假文献），避免真实联网检索；输出目录 .output/verify_resume。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from verify_p1_workflow import _QuietStubProvider

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import ChatCallbacks, LLMClient

OUTPUT_DIR = Path(".output/verify_resume")


class _Recorder:
    """记录每次工具调用（名称 + 参数），供断言不重做已完成步骤。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def callbacks(self) -> ChatCallbacks:
        cb = ChatCallbacks()
        cb.on_tool_start = lambda name, args: self.calls.append((name, args))
        return cb

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def section_calls(self) -> list[tuple[str, str]]:
        return [
            (name, str(args.get("name", "")))
            for name, args in self.calls
            if name in {"write_section", "submit_section"}
        ]


def _agent(llm: LLMClient) -> PaperAgent:
    agent = PaperAgent(llm=llm, output_dir=str(OUTPUT_DIR), style=load_profile())
    agent._toolbox.provider = _QuietStubProvider()  # 离线假文献，避免真实联网检索
    return agent


def _chat(agent: PaperAgent, text: str, recorder: _Recorder) -> str:
    return agent.chat(text, callbacks=recorder.callbacks())


def main() -> int:
    config = load_config()
    llm = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
    )
    results: list[tuple[bool, str, str]] = []
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)

    # ---- 会话 1：调研 + 提纲 + 只写第 1 节 ----
    agent1 = _agent(llm)
    recorder1 = _Recorder()
    _chat(agent1, "帮我写一篇关于大语言模型幻觉缓解的论文，正文 2000 字左右", recorder1)
    reply = _chat(
        agent1,
        "可以，开始写。本轮只写第 1 节，写完第 1 节就停下汇报进度，不要写后面的节。",
        recorder1,
    )
    session1 = agent1._toolbox.store.load() or {}
    section1 = agent1._toolbox.draft.get_section("1 引言").content
    todo1 = session1.get("todo", [])

    ok = bool(todo1) and any(s["status"] == "completed" for s in todo1)
    results.append(
        (
            ok,
            "起草过程用 update_todo 建立清单并即时落盘",
            f"清单: {[(s['content'][:12], s['status']) for s in todo1] or ['无']}",
        )
    )

    ok = bool(section1) and session1.get("outline_confirmed") is True
    results.append(
        (
            ok,
            "第 1 节完成后草稿与确认状态已持久化（模拟进程被杀）",
            f"第 1 节 {len(section1)} 字；工具: {recorder1.names()[:8]}…",
        )
    )

    # ---- 进程被杀：丢弃 agent1，全新 PaperAgent 读同一 output_dir ----
    del agent1, recorder1
    agent2 = _agent(llm)
    recorder2 = _Recorder()
    restored = agent2._toolbox.draft.get_section("1 引言").content
    system = agent2.messages[0]["content"]
    ok = (
        agent2.resumed
        and "上次会话未完成" in system
        and restored == section1
        and agent2._toolbox._outline_confirmed is True
    )
    results.append(
        (
            ok,
            "重启后恢复草稿/清单/确认状态，断点恢复块注入系统提示词",
            f"恢复第 1 节 {len(restored)} 字（一致: {restored == section1}）；"
            f"清单 {agent2._toolbox.todo.progress()}",
        )
    )

    # ---- 接续：不重做已完成步骤 ----
    _chat(agent2, "继续把剩余的节写完，然后走完收尾并导出交付。", recorder2)
    calls = recorder2.calls
    names = recorder2.names()
    # 判定线：第一个里程碑（审校/移除文献/收尾校验）之前，不得碰已完成的「1 引言」；
    # 里程碑之后按审校意见修订受影响节是流程内行为（remove_papers 要求重交受影响节），
    # 不算重做已完成步骤。
    first_gate = next(
        (i for i, (name, _) in enumerate(calls)
         if name in {"review_draft", "remove_papers", "finish_draft"}),
        len(calls),
    )
    premature = [
        n for i, (name, args) in enumerate(calls)
        if i < first_gate and name in {"write_section", "submit_section"}
        and "引言" in str(args.get("name", ""))
    ]
    revised = [
        str(args.get("name", ""))
        for name, args in calls
        if name in {"write_section", "submit_section"} and "引言" in str(args.get("name", ""))
    ]
    docx_files = list(OUTPUT_DIR.glob("**/*.docx"))
    ok = (
        "begin_draft" not in names
        and not premature
        and "finish_draft" in names
        and "export_docx" in names
        and bool(docx_files)
    )
    results.append(
        (
            ok,
            "「继续」后直接写剩余节并完成导出，接手阶段未重做第 1 节",
            f"begin_draft 复现: {'begin_draft' in names}；接手阶段重写引言: {premature or ['无']}；"
            f"全程引言修订 {len(revised)} 次（审校驱动）；docx: {len(docx_files)} 个",
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
