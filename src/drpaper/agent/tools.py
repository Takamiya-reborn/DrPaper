"""Agent 工具：把文献检索、草稿提交与 docx 导出包装成 LLM 可调用的工具。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from drpaper.export.docx_writer import export_docx
from drpaper.literature.base import SearchProvider
from drpaper.paper.draft import Draft
from drpaper.paper.markdown_parser import parse
from drpaper.skills.manager import SkillManager

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_arxiv",
            "description": "在 arXiv 检索真实文献，返回带编号的元数据。引用编号以此为准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "英文检索关键词，如 'large language model hallucination'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回条数，默认 5，最大 10",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_draft",
            "description": "提交论文正文（Markdown）。正文写完后必须先调用本工具提交全文，再调用 export_docx。",
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {
                        "type": "string",
                        "description": "完整论文正文，Markdown 格式，标题用 ## 级别",
                    },
                },
                "required": ["markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_papers",
            "description": "从文献库移除未使用的文献（按编号），编号会重排。导出被阻止且提示有未引用编号时调用，之后更新正文引用并重新 submit_draft。",
            "parameters": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要移除的文献编号列表，如 [3, 7]",
                    },
                },
                "required": ["indices"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_docx",
            "description": "把当前论文草稿导出为规范排版的 Word 文档。正文写完后必须调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "按需加载论文专精 skill 的完整内容。执行系统提示词索引中匹配的任务前必须先加载。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "skill 名称，须为索引中列出的名称",
                    },
                    "file": {
                        "type": "string",
                        "description": "可选，skill 内 references 参考文件名，如 'sections.md'",
                    },
                },
                "required": ["name"],
            },
        },
    },
]


# Windows 文件名非法字符（\ / : * ? " < > |）及其替换符
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# Windows 保留设备名，不能直接作为文件/目录名
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _sanitize_filename(name: str) -> str:
    """把论文题目转为合法的文件/目录名：替换非法字符、合并空白并去掉首尾空白与点。"""
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "未命名论文"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


# 历史消息中旧草稿的折叠阈值（字符）：超过则替换为占位符，控制上下文体积
_DRAFT_FOLD_THRESHOLD = 2000


@dataclass
class ToolBox:
    """持有会话状态（草稿、检索源、输出目录、消息历史）的工具集合。"""

    provider: SearchProvider
    draft: Draft
    output_dir: str
    skills: SkillManager
    messages: list[dict[str, Any]] = field(default_factory=list)

    def handlers(self) -> dict[str, Any]:
        """工具名 → 处理函数，供 LLM 循环执行。"""
        return {
            "search_arxiv": self._search_arxiv,
            "submit_draft": self._submit_draft,
            "remove_papers": self._remove_papers,
            "export_docx": self._export_docx,
            "load_skill": self._load_skill,
        }

    def _search_arxiv(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
        max_results = min(int(args.get("max_results", 5)), 10)
        papers = self.provider.search(query, max_results)
        numbers = self.draft.add_papers(papers)
        return json.dumps(
            {
                "query": query,
                "results": [
                    {
                        "index": numbers[paper.uid],
                        "title": paper.title,
                        "authors": paper.authors,
                        "year": paper.year,
                        "abstract": paper.abstract,
                        "url": paper.url,
                    }
                    for paper in papers
                ],
            },
            ensure_ascii=False,
        )

    def _submit_draft(self, args: dict[str, Any]) -> str:
        markdown = str(args.get("markdown", "")).strip()
        if not markdown:
            return json.dumps({"error": "markdown 不能为空"}, ensure_ascii=False)
        self._fold_old_drafts()
        self.draft.set_markdown(markdown)
        return json.dumps(
            {"status": "ok", "chars": len(self.draft.markdown)}, ensure_ascii=False
        )

    def _remove_papers(self, args: dict[str, Any]) -> str:
        raw = args.get("indices", [])
        try:
            indices = [int(n) for n in raw]
        except (TypeError, ValueError):
            return json.dumps({"error": "indices 必须是整数列表"}, ensure_ascii=False)
        return json.dumps(
            {"result": self.draft.remove_papers(indices)}, ensure_ascii=False
        )

    def _fold_old_drafts(self) -> None:
        """把历史消息中旧草稿全文替换为占位符，避免多轮修改时上下文膨胀。"""
        for msg in self.messages:
            if msg.get("role") != "assistant":
                continue
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                if (
                    fn.get("name") == "submit_draft"
                    and len(fn.get("arguments", "")) > _DRAFT_FOLD_THRESHOLD
                ):
                    fn["arguments"] = json.dumps(
                        {"markdown": "（旧稿已折叠，当前正文以最近一次提交为准）"},
                        ensure_ascii=False,
                    )

    def _export_docx(self, _args: dict[str, Any]) -> str:
        errors = self.draft.citation_errors()
        if errors:
            return json.dumps(
                {
                    "status": "blocked",
                    "errors": errors,
                    "hint": "请修正正文引用；未引用的文献可用 remove_papers 移除",
                },
                ensure_ascii=False,
            )
        name = _sanitize_filename(self.draft.title() or "未命名论文")
        out_dir = Path(self.output_dir) / name
        nodes = parse(self.draft.markdown)
        docx_path = out_dir / f"{name}.docx"
        export_docx(nodes, self.draft.references, docx_path)
        saved = self.draft.save(out_dir)
        return json.dumps(
            {
                "status": "ok",
                "docx": str(docx_path),
                "related_files": [str(p) for p in saved],
                "references": len(self.draft.references),
            },
            ensure_ascii=False,
        )

    def _load_skill(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        file = args.get("file")
        try:
            content = self.skills.load(name, str(file) if file else None)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return content
