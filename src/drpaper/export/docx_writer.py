"""docx 导出：把草稿节点渲染为规范排版的 Word 文档。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from drpaper.export.guides import guide_for_heading
from drpaper.export.style_profile import StyleProfile, load_profile
from drpaper.literature.base import Paper
from drpaper.paper.markdown_parser import (
    HeadingNode,
    Node,
    ParagraphNode,
    TitleNode,
    parse,
)

REFERENCES_HEADING = "参考文献"

ALIGN_JUSTIFY = WD_ALIGN_PARAGRAPH.JUSTIFY
ALIGN_CENTER = WD_ALIGN_PARAGRAPH.CENTER


def export_docx(
    nodes: list[Node],
    references: list[Paper],
    path: str | Path,
    profile: StyleProfile | None = None,
) -> Path:
    """按样式档案渲染草稿与文献库，写出 docx 文件；未指定档案时用默认规范。"""
    profile = profile or load_profile()
    document = Document()
    _setup_page(document, profile)

    for node in nodes:
        if isinstance(node, TitleNode):
            _add_title(document, node.text, profile)
        elif isinstance(node, HeadingNode):
            _add_heading(document, node, profile)
        elif isinstance(node, ParagraphNode):
            _add_paragraph(document, node.runs, profile)

    if references:
        _add_references(document, references, profile)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.save(out)
    return out


def _setup_page(document: Document, profile: StyleProfile) -> None:
    margin = Cm(profile.margin_cm)
    for section in document.sections:
        section.top_margin = margin
        section.bottom_margin = margin
        section.left_margin = margin
        section.right_margin = margin


def _set_fonts(run, profile: StyleProfile, cn_font: str, size_pt: float, bold: bool = False) -> None:
    """同时设置中英文字体（eastAsia 属性需手动写入 XML）。"""
    run.font.name = profile.en_font
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), cn_font)


def _add_title(document: Document, text: str, profile: StyleProfile) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = ALIGN_CENTER
    paragraph.paragraph_format.line_spacing = profile.line_spacing
    _set_fonts(paragraph.add_run(text), profile, profile.cn_heading_font, profile.title_pt, bold=True)


def _add_heading(document: Document, node: HeadingNode, profile: StyleProfile) -> None:
    size_pt = profile.heading1_pt if node.level == 1 else profile.body_pt
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.line_spacing = profile.line_spacing
    paragraph.paragraph_format.space_before = Cm(0.3)
    paragraph.paragraph_format.space_after = Cm(0.2)
    _set_fonts(paragraph.add_run(node.text), profile, profile.cn_heading_font, size_pt, bold=True)

    guide = guide_for_heading(node.text)
    if guide:
        _attach_comment(document, paragraph, guide)


def _add_paragraph(document: Document, runs: list[tuple[str, bool]], profile: StyleProfile) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = ALIGN_JUSTIFY
    paragraph.paragraph_format.line_spacing = profile.line_spacing
    paragraph.paragraph_format.first_line_indent = Pt(
        profile.body_pt * profile.first_line_indent_chars
    )
    for text, bold in runs:
        _set_fonts(paragraph.add_run(text), profile, profile.cn_body_font, profile.body_pt, bold=bold)


def _add_references(document: Document, references: list[Paper], profile: StyleProfile) -> None:
    heading = document.add_paragraph()
    heading.paragraph_format.line_spacing = profile.line_spacing
    heading.paragraph_format.space_before = Cm(0.4)
    _set_fonts(heading.add_run(REFERENCES_HEADING), profile, profile.cn_heading_font, profile.heading1_pt, bold=True)

    for index, paper in enumerate(references, start=1):
        paragraph = document.add_paragraph()
        paragraph.alignment = ALIGN_JUSTIFY
        paragraph.paragraph_format.line_spacing = profile.line_spacing
        paragraph.paragraph_format.left_indent = Cm(0.74)
        paragraph.paragraph_format.first_line_indent = Cm(-0.74)  # 悬挂缩进
        _set_fonts(paragraph.add_run(paper.reference_line(index)), profile, profile.cn_body_font, profile.reference_pt)


def _attach_comment(document: Document, paragraph, text: str) -> None:
    """在节标题段落上附加一条写作指导批注（python-docx >= 1.2）。"""
    if paragraph.runs:
        document.add_comment(paragraph.runs[0], text=text, author="DrPaper", initials="DP")
