"""草稿模型：正文 Markdown + 真实文献库，负责引用一致性与落盘。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from drpaper.literature.base import Paper
from drpaper.paper.markdown_parser import TitleNode, extract_citations, parse

# 正文中的参考文献章节：导出时由文献库统一生成，提交时移除以免重复
_REFERENCE_SECTION = re.compile(r"^\s*#{1,6}\s*参考文献\s*$.*", re.MULTILINE | re.DOTALL)


class Draft:
    """一篇论文的当前状态：正文（Markdown）与文献库（编号从 1 开始）。"""

    def __init__(self) -> None:
        self.markdown: str = ""
        self.references: list[Paper] = []
        self._uid_to_index: dict[str, int] = {}

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
            "正文中的引用编号须相应更新后重新 submit_draft。"
        )

    def reference_lines(self) -> list[str]:
        """按编号生成参考文献条目。"""
        return [p.reference_line(i + 1) for i, p in enumerate(self.references)]

    # ---- 正文 ----

    def title(self) -> str:
        """从正文提取论文题目（首个 `# 标题` 行），未写标题时返回空串。"""
        for node in parse(self.markdown):
            if isinstance(node, TitleNode):
                return node.text
        return ""

    def set_markdown(self, markdown: str) -> None:
        self.markdown = _REFERENCE_SECTION.sub("", markdown).strip() + "\n"

    def citation_errors(self) -> list[str]:
        """校验正文引用与文献库一致；返回问题描述列表（空表示通过）。"""
        if not self.markdown:
            return ["草稿为空，尚未生成正文。"]
        errors: list[str] = []
        total = len(self.references)
        cited: set[int] = set()
        for node in parse(self.markdown):
            text = getattr(node, "text", "")
            if not text:
                runs = getattr(node, "runs", [])
                text = "".join(t for t, _ in runs)
            cited |= extract_citations(text)
        for n in sorted(cited):
            if n < 1 or n > total:
                errors.append(f"正文引用了 [{n}]，但文献库中没有该编号（当前共 {total} 条）。")
        unused = [i + 1 for i in range(total) if i + 1 not in cited]
        if unused:
            errors.append(f"文献库中未被正文引用的编号：{', '.join(map(str, unused))}。")
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
