"""P2-2 验收：全流程回归（真实 LLM）。

用法：uv run python scripts/verify_p2_regression.py
三项检查：
1. 全流程走通：选题调研 → 提纲 → 用户确认 → 逐节写作 → 审校 → 收尾校验
   → 导出 docx / LaTeX / PPT 大纲 / 文献库，各产物齐全；
2. PPT 大纲有效：JSON 可解析、页数与结构完整、无署名字段；
3. 导出文件无作者元数据（沿用既有规则）：docx 核心属性 author /
   last_modified_by 等为空，LaTeX 源码无 \\author。
检索挂 stub 源（离线假文献）；输出目录 .output/verify_regression。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from verify_p1_workflow import _QuietStubProvider

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import ChatCallbacks, LLMClient

OUTPUT_DIR = Path(".output/verify_regression")


class _Recorder:
    """记录每次工具调用（名称 + 参数）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def callbacks(self) -> ChatCallbacks:
        cb = ChatCallbacks()
        cb.on_tool_start = lambda name, args: self.calls.append((name, args))
        return cb

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


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

    agent = PaperAgent(llm=llm, output_dir=str(OUTPUT_DIR), style=load_profile())
    agent._toolbox.provider = _QuietStubProvider()
    recorder = _Recorder()

    # ---- 第 1 轮：选题调研 + 提纲 ----
    agent.chat(
        "帮我写一篇关于大语言模型幻觉缓解的论文，正文 1500 字左右",
        callbacks=recorder.callbacks(),
    )
    # ---- 第 2 轮：确认提纲 + 逐节写作 + 审校 ----
    agent.chat(
        "可以，确认提纲并写完全部章节，写完审校一遍",
        callbacks=recorder.callbacks(),
    )
    # ---- 第 3 轮：收尾校验 + 导出交付 ----
    agent.chat(
        "走完收尾校验并导出 docx 与 PPT 大纲交付",
        callbacks=recorder.callbacks(),
    )
    names = recorder.names()
    title = agent.draft.title

    # ---- 检查 1：全流程关键节点齐全，产物落盘 ----
    out_dir = OUTPUT_DIR / title if title else OUTPUT_DIR
    docx_files = list(OUTPUT_DIR.glob("**/*.docx"))
    artifacts = {
        "docx": bool(docx_files),
        "references.json": bool(list(OUTPUT_DIR.glob("**/references.json"))),
        "ppt_outline.json": bool(list(OUTPUT_DIR.glob("**/ppt_outline.json"))),
        "ppt_outline.md": bool(list(OUTPUT_DIR.glob("**/ppt_outline.md"))),
    }
    ok = (
        "research_topic" in names
        and "begin_draft" in names
        and "confirm_outline" in names
        and "review_draft" in names
        and "finish_draft" in names
        and "export_docx" in names
        and all(artifacts.values())
        and not agent.draft.missing_sections()
    )
    results.append(
        (
            ok,
            "选题到导出的全流程走通，docx/PPT 大纲/文献库产物齐全",
            f"题目「{title}」{len(agent.draft.sections)} 节全部成稿；"
            f"节点: research={('research_topic' in names)}, confirm={('confirm_outline' in names)}, "
            f"review={('review_draft' in names)}, finish={('finish_draft' in names)}, "
            f"export={('export_docx' in names)}；产物: {artifacts}",
        )
    )

    # ---- 检查 2：PPT 大纲 JSON 有效且无署名字段 ----
    ppt_path = next(OUTPUT_DIR.glob("**/ppt_outline.json"), None)
    ok = False
    detail = "PPT 大纲文件缺失"
    if ppt_path:
        ppt = json.loads(ppt_path.read_text(encoding="utf-8"))
        slides = ppt.get("slides", [])
        ok = (
            len(slides) >= 3
            and all(
                {"title", "summary", "key_points"} <= set(slide) for slide in slides
            )
            and not any("author" in key.lower() for key in ppt)
        )
        detail = f"{len(slides)} 页，字段完整: {bool(slides) and all({'title', 'summary', 'key_points'} <= set(s) for s in slides)}"
    results.append((ok, "PPT 大纲 JSON 有效、页数完整、无署名字段", detail))

    # ---- 检查 3：导出文件无作者元数据 ----
    from docx import Document

    doc = Document(docx_files[0])
    props = doc.core_properties
    tex_path = next(OUTPUT_DIR.glob("**/*.tex"), None)
    tex_clean = tex_path is None or "\\author" not in tex_path.read_text(encoding="utf-8")
    ok = (
        props.author == ""
        and props.last_modified_by == ""
        and props.title == ""
        and props.comments == ""
        and tex_clean
    )
    results.append(
        (
            ok,
            "导出文件无作者元数据（docx 核心属性为空，LaTeX 无 \\author）",
            f"author={props.author!r}, last_modified_by={props.last_modified_by!r}, "
            f"title={props.title!r}; tex_clean={tex_clean}",
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
