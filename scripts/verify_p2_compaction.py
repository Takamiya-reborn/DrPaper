"""P2-1 验收：上下文压缩（真实 LLM）。

用法：uv run python scripts/verify_p2_compaction.py
三项检查：
1. 触发与继续：续写轮开头上下文占用超阈值，触发一次摘要调用把旧历史折叠为
   单条消息，折叠后 loop 照常把剩余章节写完并导出；
2. 保留要素：折叠消息里既有模型摘要（用户写作要求的原话要点），也有
   结构化权威状态（已确认提纲、目标字数、大纲预算、文献编号对照）；
3. 不遗忘：压缩后未重新 begin_draft、未重新请求确认，大纲节名与确认状态
   全程不变，最终成稿按原提纲完成。
检索挂 stub 源（离线假文献）；输出目录 .output/verify_compaction。
"""

from __future__ import annotations

import glob
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from verify_p1_workflow import _QuietStubProvider

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import ChatCallbacks, LLMClient

OUTPUT_DIR = ".output/verify_compaction"

REQUIREMENT = "写作要求：全文自称一律用「本文」，禁止用「我们」。"


class _Recorder:
    """记录工具调用与最近一次实测输入 token 数。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.last_prompt = 0

    def callbacks(self) -> ChatCallbacks:
        cb = ChatCallbacks()
        cb.on_tool_start = lambda name, args: self.calls.append((name, args))
        cb.on_usage = lambda prompt, completion, total: setattr(
            self, "last_prompt", prompt
        )
        return cb

    def names(self, since: int = 0) -> list[str]:
        return [name for name, _ in self.calls[since:]]


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

    agent = PaperAgent(llm=llm, output_dir=OUTPUT_DIR, style=load_profile())
    agent._toolbox.provider = _QuietStubProvider()
    recorder = _Recorder()

    # ---- 阶段 A：调研 + 提纲 + 确认 + 写前两节（不触发压缩的正常窗口）----
    agent.chat(
        f"帮我写一篇关于大语言模型幻觉缓解的论文，正文 1200 字左右。{REQUIREMENT}",
        callbacks=recorder.callbacks(),
    )
    agent.chat(
        "可以，确认提纲。先写完前两节就停下汇报进度。",
        callbacks=recorder.callbacks(),
    )
    outline_before = [s.name for s in agent.draft.sections]
    title_before = agent.draft.title
    assert title_before and len(outline_before) >= 3, "阶段 A 未产出提纲"

    # ---- 阶段 B：压低触发线，续写轮开头上下文占用必然超阈值 ----
    # 触发线取「最近实测输入 × 0.9」：必触发，且折叠后的稳态占用（系统提示词
    # + 摘要 + 保留尾）远低于触发线，不会逐轮反复压缩
    agent._compactor.window_tokens = int(recorder.last_prompt * 0.9 / 0.7)
    compactions_before = agent._compactor.compactions
    calls_before = len(recorder.calls)
    agent.chat(
        "继续写完剩余章节，走完收尾校验并导出 docx 交付。",
        callbacks=recorder.callbacks(),
    )

    # ---- 检查 1：触发一次摘要折叠，折叠后任务照常完成 ----
    folded = next(
        (m["content"] for m in agent.messages[1:] if "压缩摘要" in str(m.get("content"))),
        "",
    )
    docx_files = glob.glob(f"{OUTPUT_DIR}/**/*.docx", recursive=True)
    missing = agent.draft.missing_sections()
    names = recorder.names(since=calls_before)
    ok = (
        agent._compactor.compactions > compactions_before
        and bool(folded)
        and not missing
        and "begin_draft" not in names
        and "export_docx" in names
        and bool(docx_files)
    )
    results.append(
        (
            ok,
            "续写轮触发压缩，折叠后照常写完全部章节并导出",
            f"压缩 {agent._compactor.compactions - compactions_before} 次；"
            f"折叠消息 {len(folded)} 字；缺节: {missing or ['无']}；"
            f"docx: {len(docx_files)} 个",
        )
    )

    # ---- 检查 2：折叠消息保留要素（摘要 + 权威状态）----
    outline_lines = [s.name for s in agent.draft.sections]
    ok = (
        "权威状态" in folded
        and "提纲已确认" in folded
        and ("自称" in folded or "「本文」" in folded)  # 用户写作要求进摘要
        and all(name in folded for name in outline_lines)  # 大纲节名保留
    )
    results.append(
        (
            ok,
            "折叠消息同时保留用户写作要求与结构化权威状态",
            f"权威状态: {'权威状态' in folded}；提纲确认: {'提纲已确认' in folded}；"
            f"写作要求: {'自称' in folded or '「本文」' in folded}；"
            f"节名齐全: {all(name in folded for name in outline_lines)}",
        )
    )

    # ---- 检查 3：压缩后不遗忘（不重做、不改大纲、确认状态保持）----
    outline_after = [s.name for s in agent.draft.sections]
    ok = (
        title_before == agent.draft.title
        and outline_before == outline_after
        and agent._toolbox._outline_confirmed is True
        and all(s.content for s in agent.draft.sections)
    )
    results.append(
        (
            ok,
            "压缩后未重新 begin_draft/确认，原提纲全程一致且全部成稿",
            f"题目一致: {title_before == agent.draft.title}；"
            f"大纲一致: {outline_before == outline_after}；"
            f"各节成稿: {all(s.content for s in agent.draft.sections)}",
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
