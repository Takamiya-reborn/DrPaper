"""Markdown 子集解析：将草稿文本解析为结构化节点，供导出层渲染。

支持的语法子集：
- `# 标题`   —— 论文题目
- `## 节`    —— 一级节标题
- `### 小节` —— 二级节标题
- 普通段落   —— 支持 `**粗体**` 行内标记与 `[n]` 引用标记
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


Node = TitleNode | HeadingNode | ParagraphNode

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_CITATION_PATTERN = re.compile(r"\[(\d{1,3}(?:,\s*\d{1,3})*)\]")


def parse(markdown: str) -> list[Node]:
    """把草稿 Markdown 解析为节点列表。"""
    nodes: list[Node] = []
    buffer: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            _flush(buffer, nodes)
            nodes.append(HeadingNode(text=line[4:].strip(), level=2))
        elif line.startswith("## "):
            _flush(buffer, nodes)
            nodes.append(HeadingNode(text=line[3:].strip(), level=1))
        elif line.startswith("# "):
            _flush(buffer, nodes)
            nodes.append(TitleNode(text=line[2:].strip()))
        elif line.strip():
            buffer.append(line.strip())
        else:
            _flush(buffer, nodes)
    _flush(buffer, nodes)
    return nodes


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
