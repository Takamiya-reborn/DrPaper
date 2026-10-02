"""docx 导出：把草稿节点渲染为规范排版的 Word 文档。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm

from drpaper.export.styles import (
    ALIGN_CENTER,
    ALIGN_JUSTIFY,
    CN_BODY_FONT,
    CN_HEADING_FONT,
    EN_FONT,
    FIRST_LINE_INDENT_CHARS,
    LINE_SPACING,
    PAGE_MARGINS,
    SIZE_BODY,
    SIZE_HEADING_1,
    SIZE_REFERENCE,
    SIZE_TITLE,
    guide_for_heading,
)
from drpaper.literature.base import Paper
from drpaper.paper.markdown_parser import (
    HeadingNode,
    Node,
    ParagraphNode,
    TitleNode,
    parse,
)

REFERENCES_HEADING = "参考文献"


def export_docx(nodes: list[Node], references: list[Paper], path: str | Path) -> Path:
    """按学术格式渲染草稿与文献库，写出 docx 文件。"""
    document = Document()
    _setup_page(document)

    for node in nodes:
        if isinstance(node, TitleNode):
            _add_title(document, node.text)
        elif isinstance(node, HeadingNode):
            _add_heading(document, node)
        elif isinstance(node, ParagraphNode):
            _add_paragraph(document, node.runs)

    if references:
        _add_references(document, references)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.save(out)
    return out


def _setup_page(document: Document) -> None:
    for section in document.sections:
        section.top_margin = PAGE_MARGINS
        section.bottom_margin = PAGE_MARGINS
        section.left_margin = PAGE_MARGINS
        section.right_margin = PAGE_MARGINS


def _set_fonts(run, cn_font: str, size, bold: bool = False) -> None:
    """同时设置中英文字体（eastAsia 属性需手动写入 XML）。"""
    run.font.name = EN_FONT
    run.font.size = size
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), cn_font)


def _add_title(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = ALIGN_CENTER
    paragraph.paragraph_format.line_spacing_rule = LINE_SPACING
    _set_fonts(paragraph.add_run(text), CN_HEADING_FONT, SIZE_TITLE, bold=True)


def _add_heading(document: Document, node: HeadingNode) -> None:
    size = SIZE_HEADING_1 if node.level == 1 else SIZE_BODY
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.line_spacing_rule = LINE_SPACING
    paragraph.paragraph_format.space_before = Cm(0.3)
    paragraph.paragraph_format.space_after = Cm(0.2)
    _set_fonts(paragraph.add_run(node.text), CN_HEADING_FONT, size, bold=True)

    guide = guide_for_heading(node.text)
    if guide:
        _attach_comment(document, paragraph, guide)


def _add_paragraph(document: Document, runs: list[tuple[str, bool]]) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = ALIGN_JUSTIFY
    paragraph.paragraph_format.line_spacing_rule = LINE_SPACING
    paragraph.paragraph_format.first_line_indent = SIZE_BODY * FIRST_LINE_INDENT_CHARS
    for text, bold in runs:
        _set_fonts(paragraph.add_run(text), CN_BODY_FONT, SIZE_BODY, bold=bold)


def _add_references(document: Document, references: list[Paper]) -> None:
    heading = document.add_paragraph()
    heading.paragraph_format.line_spacing_rule = LINE_SPACING
    heading.paragraph_format.space_before = Cm(0.4)
    _set_fonts(heading.add_run(REFERENCES_HEADING), CN_HEADING_FONT, SIZE_HEADING_1, bold=True)

    for index, paper in enumerate(references, start=1):
        paragraph = document.add_paragraph()
        paragraph.alignment = ALIGN_JUSTIFY
        paragraph.paragraph_format.line_spacing_rule = LINE_SPACING
        paragraph.paragraph_format.left_indent = Cm(0.74)
        paragraph.paragraph_format.first_line_indent = Cm(-0.74)  # 悬挂缩进
        _set_fonts(paragraph.add_run(paper.reference_line(index)), CN_BODY_FONT, SIZE_REFERENCE)


def _attach_comment(document: Document, paragraph, text: str) -> None:
    """在节标题段落上附加一条写作指导批注（python-docx >= 1.2）。"""
    if paragraph.runs:
        document.add_comment(paragraph.runs[0], text=text, author="DrPaper", initials="DP")
