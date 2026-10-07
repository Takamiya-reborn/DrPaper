"""LaTeX 导出：把草稿节点渲染为可编译的 .tex 源码（pylatex）。

与 docx 导出共用同一份节点输入；行内/独立公式原样进入 LaTeX，
中文用 ctexart 文档类（编译需本机装有 XeLaTeX）。
"""

from __future__ import annotations

from pathlib import Path

from pylatex import Command, Document, NoEscape, Section, Subsection, Table, Tabular

from drpaper.literature.base import Paper
from drpaper.paper.markdown_parser import (
    HeadingNode,
    MathNode,
    Node,
    ParagraphNode,
    Run,
    TableNode,
    TitleNode,
    parse,
)

# LaTeX 特殊字符转义（公式片段原样保留，不转义）
_ESCAPES = str.maketrans(
    {
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "$": r"\$",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)


def export_tex(nodes: list[Node], references: list[Paper], path: str | Path) -> Path:
    """渲染草稿与文献库为 LaTeX 源码，写出 .tex 文件。"""
    document = Document(
        documentclass="ctexart",
        # ctex + XeLaTeX 自管编码与字体，去掉 pylatex 默认附加的西文编码包
        fontenc=None,
        inputenc=None,
        lmodern=False,
        textcomp=False,
    )
    document.preamble.append(Command("usepackage", "booktabs"))

    for node in nodes:
        if isinstance(node, TitleNode):
            document.preamble.append(Command("title", NoEscape(node.text.translate(_ESCAPES))))
            document.append(Command("maketitle"))
        elif isinstance(node, HeadingNode):
            cls = Section if node.level == 1 else Subsection
            document.append(cls(node.text, numbering=False))
        elif isinstance(node, ParagraphNode):
            document.append(NoEscape(_runs_to_tex(node.runs) + "\n"))
        elif isinstance(node, MathNode):
            document.append(NoEscape(f"\\[{node.text}\\]\n"))
        elif isinstance(node, TableNode):
            _add_table(document, node)

    document.append(NoEscape(r"\section*{参考文献}" + "\n"))
    for index, paper in enumerate(references, start=1):
        line = paper.reference_line(index).translate(_ESCAPES)
        document.append(NoEscape(line + "\n"))

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # pylatex 会在传入路径后追加 .tex，须去掉后缀传入
    document.generate_tex(str(out.with_suffix("")))
    return out


def _runs_to_tex(runs: list[Run]) -> str:
    """行内片段序列 → LaTeX 片段：粗体加 \\textbf，公式包 $...$，文本转义。"""
    parts = []
    for run in runs:
        if run.math:
            parts.append(f"${run.text}$")
        elif run.bold:
            parts.append(r"\textbf{" + run.text.translate(_ESCAPES) + "}")
        else:
            parts.append(run.text.translate(_ESCAPES))
    return "".join(parts)


def _add_table(document: Document, node: TableNode) -> None:
    """渲染学术三线表（booktabs）：表题由 caption 命令生成。"""
    spec = "c" * len(node.header)
    with document.create(Table(position="h")) as table:
        if node.caption:
            table.add_caption(node.caption)
        with table.create(Tabular(spec)) as tabular:
            tabular.append(NoEscape(r"\toprule"))
            tabular.add_row(*node.header)
            tabular.append(NoEscape(r"\midrule"))
            for cells in node.rows:
                tabular.add_row(*cells[: len(node.header)])
            tabular.append(NoEscape(r"\bottomrule"))
