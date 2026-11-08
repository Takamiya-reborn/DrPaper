"""P2-2 验证：清理与回归（离线，假流式客户端驱动真实全流程）。

对应 plan.md P2-2：
- 全流程回归：目标字数 → 提纲 → 确认 → 逐节提交 → 收尾校验 → 导出
  （docx / LaTeX / PPT 大纲 / 文献库各产物齐全）
- 导出文件无作者元数据（沿用既有规则）：docx 核心属性的 author /
  last_modified_by 等字段为空，LaTeX 源码无 \\author，PPT 大纲无署名字段
"""

from __future__ import annotations

import json

from docx import Document
from fake_llm import agent_with, chunk, tool_call, tool_results

PPT_JSON = {
    "title": "测试论文汇报",
    "audience": "学术汇报",
    "estimated_minutes": 10,
    "slides": [
        {
            "number": 1,
            "title": "研究背景",
            "summary": "介绍研究背景。",
            "key_points": ["要点一", "要点二", "要点三"],
            "evidence": [],
            "visual_suggestion": "示意图",
            "speaker_notes": "讲稿提示。",
        },
        {
            "number": 2,
            "title": "方法概述",
            "summary": "介绍方法。",
            "key_points": ["要点一", "要点二"],
            "evidence": [],
            "visual_suggestion": "流程图",
            "speaker_notes": "讲稿提示。",
        },
    ],
    "references": [],
}

_SECTIONS = [{"name": "1 引言", "budget": 300}, {"name": "2 方法", "budget": 300}]


def _flow_rounds() -> list[list]:
    """选题→提纲→确认→逐节提交→收尾→导出的完整轮次脚本。

    export_docx 内部会调一次 PPT 大纲模型（max_rounds=1），消耗 JSON 轮。
    """
    return [
        # 第 1 轮对话：目标字数 + 提纲
        [chunk(tool_calls=[tool_call("set_target_chars", {"target_chars": 600})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("begin_draft", {"title": "回归测试论文", "sections": _SECTIONS})], finish="tool_calls")],
        [chunk(content="以上是提纲，请确认。", finish="stop")],
        # 第 2 轮对话：确认 + 逐节提交 + 收尾 + 导出
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 300})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("submit_section", {"name": "2 方法", "content": "法" * 300})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("finish_draft", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("export_docx", {"include_guides": False})], finish="tool_calls")],
        [chunk(content=json.dumps(PPT_JSON, ensure_ascii=False), finish="stop")],  # PPT 大纲调用
        [chunk(content="已导出交付。", finish="stop")],
    ]


def test_full_flow_exports_all_artifacts(tmp_path) -> None:
    agent = agent_with(_flow_rounds(), tmp_path)
    agent.chat("帮我写一篇 600 字的测试论文")
    agent.chat("可以，开始写，写完导出")

    results = tool_results(agent.messages)
    statuses = [r.get("status") for r in results]
    assert "pending_confirmation" in statuses  # 提纲确认门禁照常生效
    assert statuses.count("ok") >= 5  # 确认/两节/收尾/导出全部通过

    out_dir = tmp_path / "回归测试论文"
    docx_path = out_dir / "回归测试论文.docx"
    assert docx_path.exists()
    assert (out_dir / "references.json").exists()
    assert (out_dir / "ppt_outline.json").exists()
    assert (out_dir / "ppt_outline.md").exists()
    assert (out_dir / "回归测试论文.tex").exists()
    assert (tmp_path / "session.json").exists()  # 断点续跑快照也随流程落盘


def test_docx_has_no_author_metadata(tmp_path) -> None:
    """沿用既有规则：导出 docx 的核心属性无作者等元数据，避免错误署名。"""
    agent = agent_with(_flow_rounds(), tmp_path)
    agent.chat("帮我写一篇 600 字的测试论文")
    agent.chat("可以，开始写，写完导出")

    doc = Document(tmp_path / "回归测试论文" / "回归测试论文.docx")
    props = doc.core_properties
    assert props.author == ""
    assert props.last_modified_by == ""
    assert props.title == ""
    assert props.subject == ""
    assert props.keywords == ""
    assert props.comments == ""
    # 正文里能找到论文题目（渲染完整）
    texts = [p.text for p in doc.paragraphs]
    assert "回归测试论文" in texts


def test_tex_and_ppt_have_no_author_metadata(tmp_path) -> None:
    agent = agent_with(_flow_rounds(), tmp_path)
    agent.chat("帮我写一篇 600 字的测试论文")
    agent.chat("可以，开始写，写完导出")

    out_dir = tmp_path / "回归测试论文"
    tex = (out_dir / "回归测试论文.tex").read_text(encoding="utf-8")
    assert "\\author" not in tex  # LaTeX 源码不写作者
    assert "\\title" in tex

    ppt = json.loads((out_dir / "ppt_outline.json").read_text(encoding="utf-8"))
    assert len(ppt["slides"]) == len(PPT_JSON["slides"])
    assert not any("author" in key.lower() for key in ppt)  # PPT 大纲无署名字段
    markdown = (out_dir / "ppt_outline.md").read_text(encoding="utf-8")
    assert "作者" not in markdown
