"""草稿模型：题目 + 分节正文 + 真实文献库，负责引用一致性与落盘。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from drpaper.literature.base import Paper
from drpaper.paper.markdown_parser import (
    HeadingNode,
    MathNode,
    ParagraphNode,
    TableNode,
    TitleNode,
    extract_citations,
    parse,
)
from drpaper.paper.wordcount import count_chars

# 这些节渲染为粗体段落而非节标题（与结构模板一致）
_BOLD_SECTIONS = {"摘要", "关键词"}

# 节字数/全文字数允许低于预算/目标的比例下限
MIN_BUDGET_RATIO = 0.85

# 节字数允许超出预算的比例上限（防止正文漂向模型自然尺度）
MAX_BUDGET_RATIO = 1.3


def _node_text(
    node: TitleNode | HeadingNode | ParagraphNode | MathNode | TableNode,
) -> str:
    """提取节点中的纯文本（含表格表题与单元格），供引用校验与统计。

    公式片段（LaTeX 源码）不参与提取，避免源码字符干扰引用与断言扫描。
    """
    if isinstance(node, TableNode):
        cells = [node.caption, *node.header, *(c for row in node.rows for c in row)]
        return " ".join(cells)
    if isinstance(node, (TitleNode, HeadingNode)):
        return node.text
    if isinstance(node, MathNode):
        return ""
    return "".join(r.text for r in node.runs if not r.math)


@dataclass
class Section:
    """大纲中的一节：名称、字数预算与已提交内容。"""

    name: str
    budget: int  # 字数预算；0 表示不限字数（如"关键词"）
    content: str = ""

    def too_short(self, content: str) -> bool:
        """提交内容是否低于字数预算下限；不限字数的节恒为否。"""
        return self.budget > 0 and count_chars(content) < self.budget * MIN_BUDGET_RATIO

    def too_long(self, content: str) -> bool:
        """提交内容是否超出字数预算上限；不限字数的节恒为否。"""
        return self.budget > 0 and count_chars(content) > self.budget * MAX_BUDGET_RATIO


def parse_outline(raw: Any) -> list[Section]:
    """把 LLM 提交的大纲 JSON（[{name, budget}, ...]）解析为 Section 列表。

    节名为空或重复时抛 ValueError；budget 为负数时截为 0。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("sections 不能为空")
    names = [str(s.get("name", "")).strip() for s in raw]
    if any(not name for name in names):
        raise ValueError("存在空节名")
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise ValueError(f"节名重复: {'、'.join(duplicated)}")
    return [
        Section(name=name, budget=max(0, int(s.get("budget", 0))))
        for name, s in zip(names, raw)
    ]


class Draft:
    """一篇论文的当前状态：题目、分节正文与文献库（编号从 1 开始）。"""

    def __init__(self) -> None:
        self.title: str = ""
        self.sections: list[Section] = []
        self.markdown: str = ""
        self.references: list[Paper] = []
        self._uid_to_index: dict[str, int] = {}

    # ---- 大纲与分节 ----

    def begin(self, title: str, sections: list[Section]) -> None:
        """重置题目与分节状态，重新起草；文献库保留。"""
        self.title = title
        self.sections = sections

    def get_section(self, name: str) -> Section:
        """按名称精确取节，找不到时抛出带可用节名的 KeyError。"""
        for section in self.sections:
            if section.name == name:
                return section
        names = "、".join(s.name for s in self.sections)
        raise KeyError(f"节名不在大纲中: {name}（可用节名: {names}）")

    def missing_sections(self) -> list[str]:
        """尚未提交内容的节名列表。"""
        return [s.name for s in self.sections if not s.content]

    def section_chars(self, name: str) -> int:
        """某节实际字数。"""
        return count_chars(self.get_section(name).content)

    def total_chars(self) -> int:
        """全文实际字数（按各节已提交内容统计）。"""
        return sum(count_chars(s.content) for s in self.sections)

    def short_sections(self) -> list[Section]:
        """字数低于预算下限的节，按大纲顺序（防御性复核用）。"""
        return [s for s in self.sections if s.too_short(s.content)]

    def long_sections(self) -> list[Section]:
        """字数超出预算上限的节，按大纲顺序（防御性复核用）。"""
        return [s for s in self.sections if s.too_long(s.content)]

    def total_too_short(self, target: int) -> bool:
        """全文字数是否低于目标字数下限。"""
        return self.total_chars() < target * MIN_BUDGET_RATIO

    def build_markdown(self) -> None:
        """按大纲顺序拼接全文写入 self.markdown。

        首行为 `# 题目`；摘要/关键词渲染为粗体段落，其余节为 `## 节名`。
        """
        parts = [f"# {self.title}"]
        for section in self.sections:
            if section.name in _BOLD_SECTIONS:
                parts.append(f"**{section.name}**：{section.content}")
            else:
                parts.append(f"## {section.name}\n\n{section.content}")
        self.markdown = "\n\n".join(parts) + "\n"

    # ---- 文献库 ----

    def add_papers(self, papers: list[Paper]) -> dict[str, int]:
        """把检索到的文献登记入库，返回 uid → 引用编号（已入库的保持原编号）。"""
        assigned: dict[str, int] = {}
        for paper in papers:
            if paper.uid in self._uid_to_index:
                assigned[paper.uid] = self._uid_to_index[paper.uid]
                continue
            self.references.append(paper)
            self._uid_to_index[paper.uid] = len(self.references)
            assigned[paper.uid] = len(self.references)
        return assigned

    def remove_papers(self, indices: list[int]) -> str:
        """移除指定编号的文献并重新编号，返回给模型的结果说明。

        编号重排后正文中的引用必须相应更新，说明文字里明确提示这一点。
        """
        remove = set(indices)
        invalid = sorted(n for n in remove if not 1 <= n <= len(self.references))
        if invalid:
            return f"编号超出文献库范围（1~{len(self.references)}）: {invalid}"
        kept = [p for i, p in enumerate(self.references, start=1) if i not in remove]
        removed = len(self.references) - len(kept)
        if removed == 0:
            return "没有文献被移除。"
        self.references = kept
        self._uid_to_index = {p.uid: i for i, p in enumerate(kept, start=1)}
        return (
            f"已移除 {removed} 条文献，剩余 {len(kept)} 条，编号已重排。"
            "请更新各节正文中的引用编号后重新 submit_section 提交受影响的节，再 finish_draft。"
        )

    def reference_lines(self) -> list[str]:
        """按编号生成参考文献条目。"""
        return [p.reference_line(i + 1) for i, p in enumerate(self.references)]

    # ---- 引用校验 ----

    def citation_errors(self) -> list[str]:
        """校验正文引用与文献库一致；返回问题描述列表（空表示通过）。"""
        if not self.markdown:
            return ["草稿为空，尚未生成正文。"]
        errors: list[str] = []
        total = len(self.references)
        cited: set[int] = set()
        for node in parse(self.markdown):
            text = _node_text(node)
            if text:
                cited |= extract_citations(text)
        for n in sorted(cited):
            if n < 1 or n > total:
                errors.append(
                    f"正文引用了 [{n}]，但文献库中没有该编号（当前共 {total} 条）。"
                )
        unused = [i + 1 for i in range(total) if i + 1 not in cited]
        if unused:
            errors.append(
                f"文献库中未被正文引用的编号：{', '.join(map(str, unused))}。"
            )
        return errors

    # ---- 落盘 ----

    def save(self, output_dir: str) -> list[Path]:
        """保存草稿与文献库，返回写入的文件路径。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "draft.md"
        md_path.write_text(self.markdown, encoding="utf-8")
        ref_path = out / "references.json"
        ref_path.write_text(
            json.dumps(
                [asdict(paper) for paper in self.references],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return [md_path, ref_path]

    def save_references(self, output_dir: str | Path) -> Path:
        """保存文献库；最终交付导出不再写入原稿文件。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ref_path = out / "references.json"
        ref_path.write_text(
            json.dumps(
                [asdict(paper) for paper in self.references],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ref_path
