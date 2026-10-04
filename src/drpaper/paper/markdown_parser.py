"""Markdown 子集解析：将草稿文本解析为结构化节点，供导出层渲染。

支持的语法子集：
- `# 标题`   —— 论文题目
- `## 节`    —— 一级节标题
- `### 小节` —— 二级节标题
- 普通段落   —— 支持 `**粗体**` 行内标记与 `[n]` 引用标记
- 管道表格   —— 表题行（`表 N：…`）+ 表头行 + `|---|` 分隔行 + 数据行
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TitleNode:
    text: str
    kind: Literal["title"] = "title"


@dataclass(frozen=True)
class HeadingNode:
    text: str
    level: int  # 1 = 一级节标题（##），2 = 二级节标题（###）
    kind: Literal["heading"] = "heading"


@dataclass(frozen=True)
class ParagraphNode:
    """一个段落，runs 保留粗体行内格式：(文本, 是否加粗)。"""

    runs: list[tuple[str, bool]]
    kind: Literal["paragraph"] = "paragraph"


@dataclass(frozen=True)
class TableNode:
    """一个表格：表题（可为空）、表头与数据行。"""

    caption: str
    header: list[str]
    rows: list[list[str]]
    kind: Literal["table"] = "table"


Node = TitleNode | HeadingNode | ParagraphNode | TableNode

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_CITATION_PATTERN = re.compile(r"\[(\d{1,3}(?:,\s*\d{1,3})*)\]")

# 表题行：如"表 1：各方法性能对比"
_CAPTION_PATTERN = re.compile(r"^表\s*\d*\s*[:：]")


def parse(markdown: str) -> list[Node]:
    """把草稿 Markdown 解析为节点列表。"""
    nodes: list[Node] = []
    buffer: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("### "):
            _flush(buffer, nodes)
            nodes.append(HeadingNode(text=line[4:].strip(), level=2))
        elif line.startswith("## "):
            _flush(buffer, nodes)
            nodes.append(HeadingNode(text=line[3:].strip(), level=1))
        elif line.startswith("# "):
            _flush(buffer, nodes)
            nodes.append(TitleNode(text=line[2:].strip()))
        elif _is_table_start(line, lines[i + 1 : i + 2]):
            _flush(buffer, nodes)
            caption = _pop_caption(nodes)
            i = _parse_table(line, lines[i + 1 :], nodes, caption) + i
        elif line.strip():
            buffer.append(line.strip())
        else:
            _flush(buffer, nodes)
        i += 1
    _flush(buffer, nodes)
    return nodes


def _is_table_start(line: str, next_lines: list[str]) -> bool:
    """该行是否为表格首行（管道行且次行为分隔行）。"""
    if not line.lstrip().startswith("|"):
        return False
    return bool(next_lines) and _is_separator(next_lines[0].strip())


def _is_separator(line: str) -> bool:
    """是否为 Markdown 表格分隔行，如 `| --- | --- |`。"""
    return bool(re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line))


def _pop_caption(nodes: list[Node]) -> str:
    """若节点列表末尾是表题段落（如"表 1：…"）则弹出并返回其文本，否则返回空串。

    表题与表格间允许有空行：表题先被 flush 成普通段落，此处回收挂到表格上。
    """
    if nodes and isinstance(nodes[-1], ParagraphNode):
        runs = nodes[-1].runs
        if len(runs) == 1 and not runs[0][1] and _CAPTION_PATTERN.match(runs[0][0]):
            return nodes.pop().runs[0][0]
    return ""


def _parse_table(first: str, rest: list[str], nodes: list[Node], caption: str) -> int:
    """从表头行起收集整个表块，追加 TableNode；返回已消费的行数（不含表头行）。"""
    header = _split_row(first)
    rows: list[list[str]] = []
    consumed = 0
    for line in rest:
        stripped = line.strip()
        if _is_separator(stripped):
            consumed += 1
            continue
        if not stripped.startswith("|"):
            break
        rows.append(_split_row(stripped))
        consumed += 1
    nodes.append(TableNode(caption=caption, header=header, rows=rows))
    return consumed


def _split_row(line: str) -> list[str]:
    """把表格行按竖线拆成单元格，去掉首尾空管道与单元格空白。"""
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


def parse_runs(text: str) -> list[tuple[str, bool]]:
    """把段落文本拆为 (文本, 是否加粗) 行内片段序列。"""
    runs: list[tuple[str, bool]] = []
    pos = 0
    for match in _BOLD_PATTERN.finditer(text):
        if match.start() > pos:
            runs.append((text[pos:match.start()], False))
        runs.append((match.group(1), True))
        pos = match.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs or [(text, False)]


def extract_citations(text: str) -> set[int]:
    """提取文本中的引用编号，如 `[1]`、`[2,3]`。"""
    numbers: set[int] = set()
    for match in _CITATION_PATTERN.finditer(text):
        for part in match.group(1).split(","):
            numbers.add(int(part.strip()))
    return numbers


def _flush(buffer: list[str], nodes: list[Node]) -> None:
    if buffer:
        nodes.append(ParagraphNode(runs=parse_runs(" ".join(buffer))))
        buffer.clear()
