"""Agent 工具：把文献检索、分节起草与 docx 导出包装成 LLM 可调用的工具。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from drpaper.export.docx_writer import export_docx
from drpaper.literature.base import SearchProvider
from drpaper.paper.draft import Draft, Section
from drpaper.paper.markdown_parser import extract_citations, parse
from drpaper.paper.wordcount import count_chars
from drpaper.skills.manager import SkillManager

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_arxiv",
            "description": "在 arXiv 检索真实文献。默认登记入文献库并返回引用编号（起草引用以此为准）；"
            "咨询类任务（问方向、荐论文、查新）传 register=false，结果不入库。",
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
                    "register": {
                        "type": "boolean",
                        "description": "是否登记入文献库；仅咨询调研时传 false，默认 true",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "begin_draft",
            "description": "开始起草论文：提交题目、大纲与各节字数预算。各节预算之和应等于目标字数。"
            "之后逐节 submit_section，全部完成后 finish_draft。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "论文题目（一行，简洁具体，含方法或结论关键词）",
                    },
                    "sections": {
                        "type": "array",
                        "description": "大纲节列表，按顺序；'摘要'、'关键词' 为固定节",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "节名，如 '1 引言'",
                                },
                                "budget": {
                                    "type": "integer",
                                    "description": "该节字数预算；不限字数的节（关键词）填 0",
                                },
                            },
                            "required": ["name", "budget"],
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_section",
            "description": "提交一节正文（Markdown 段落，不含节标题行）。"
            "name 必须与 begin_draft 中的节名完全一致；同名重复提交为覆盖修改。"
            "每节字数低于预算 85% 会被拒绝，须自行扩写后重交，禁止反问用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节名，与大纲中完全一致"},
                    "content": {
                        "type": "string",
                        "description": "该节正文段落，可含 [n] 引用标记，不含节标题行",
                    },
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_draft",
            "description": "全部节提交后调用：校验各节齐全、总字数与引用一致性，拼接全文。通过后才可 export_docx。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_papers",
            "description": "从文献库移除未使用的文献（按编号），编号会重排。导出被阻止且提示有未引用编号时调用，"
            "之后更新各节引用并重新 submit_section 提交受影响的节，再 finish_draft。",
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
            "description": "把当前论文草稿导出为规范排版的 Word 文档。finish_draft 通过后调用。",
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


# 节字数下限占预算的比例
_SECTION_MIN_RATIO = 0.85

# 总字数下限占目标字数的比例
_TOTAL_MIN_RATIO = 0.85

# 历史消息中旧分节正文的折叠阈值（字符）：分节参数普遍 1~2k 字，阈值须收紧才有效
_SECTION_FOLD_THRESHOLD = 800

_TOO_SHORT_HINT = "字数不足。请自行扩写本节（补充机制细节、实例与文献综述深度）后重新 submit_section，禁止向用户询问是否补充。"


@dataclass
class ToolBox:
    """持有会话状态（草稿、检索源、输出目录、消息历史）的工具集合。"""

    provider: SearchProvider
    draft: Draft
    output_dir: str
    skills: SkillManager
    messages: list[dict[str, Any]] = field(default_factory=list)
    target_chars: int = 5000

    def handlers(self) -> dict[str, Any]:
        """工具名 → 处理函数，供 LLM 循环执行。"""
        return {
            "search_arxiv": self._search_arxiv,
            "begin_draft": self._begin_draft,
            "submit_section": self._submit_section,
            "finish_draft": self._finish_draft,
            "remove_papers": self._remove_papers,
            "export_docx": self._export_docx,
            "load_skill": self._load_skill,
        }

    def _search_arxiv(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
        max_results = min(int(args.get("max_results", 5)), 10)
        register = args.get("register", True) is not False
        papers = self.provider.search(query, max_results)
        numbers = self.draft.add_papers(papers) if register else None
        return json.dumps(
            {
                "query": query,
                "results": [
                    {
                        **({"index": numbers[paper.uid]} if register else {}),
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

    def _begin_draft(self, args: dict[str, Any]) -> str:
        title = str(args.get("title", "")).strip()
        raw_sections = args.get("sections")
        if not title:
            return json.dumps({"error": "title 不能为空"}, ensure_ascii=False)
        if not isinstance(raw_sections, list) or not raw_sections:
            return json.dumps({"error": "sections 不能为空"}, ensure_ascii=False)
        names = [str(s.get("name", "")).strip() for s in raw_sections]
        if any(not name for name in names):
            return json.dumps({"error": "存在空节名"}, ensure_ascii=False)
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            return json.dumps(
                {"error": f"节名重复: {'、'.join(duplicated)}"}, ensure_ascii=False
            )
        sections = [
            Section(name=name, budget=max(0, int(s.get("budget", 0))))
            for name, s in zip(names, raw_sections)
        ]
        result: dict[str, Any] = {
            "status": "ok",
            "sections": [{"name": s.name, "budget": s.budget} for s in sections],
            "target_chars": self.target_chars,
        }
        total_budget = sum(s.budget for s in sections)
        deviation = abs(total_budget - self.target_chars) / max(self.target_chars, 1)
        if deviation > 0.2:
            result["warning"] = (
                f"各节预算之和 {total_budget} 与目标字数 {self.target_chars} 偏差较大，请调整大纲"
            )
        self.draft.begin(title, sections)
        return json.dumps(result, ensure_ascii=False)

    def _submit_section(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        content = str(args.get("content", "")).strip()
        if not content:
            return json.dumps({"error": "content 不能为空"}, ensure_ascii=False)
        try:
            section = self.draft.get_section(name)
        except KeyError as exc:
            return json.dumps(
                {"error": str(exc), "missing": self.draft.missing_sections()},
                ensure_ascii=False,
            )
        count = count_chars(content)
        if section.budget > 0 and count < section.budget * _SECTION_MIN_RATIO:
            return json.dumps(
                {
                    "status": "too_short",
                    "section": name,
                    "actual": count,
                    "budget": section.budget,
                    "hint": _TOO_SHORT_HINT,
                },
                ensure_ascii=False,
            )
        section.content = content
        result: dict[str, Any] = {
            "status": "ok",
            "section": name,
            "chars": count,
            "budget": section.budget,
            "progress": f"已完成 {len(self.draft.sections) - len(self.draft.missing_sections())}"
            f"/{len(self.draft.sections)} 节",
        }
        total_refs = len(self.draft.references)
        over = sorted(n for n in extract_citations(content) if n < 1 or n > total_refs)
        if over:
            result["warning"] = f"引用编号超出文献库范围: {over}（当前共 {total_refs} 条）"
        return json.dumps(result, ensure_ascii=False)

    def _finish_draft(self, _args: dict[str, Any]) -> str:
        missing = self.draft.missing_sections()
        if missing:
            return json.dumps(
                {
                    "status": "incomplete",
                    "missing": missing,
                    "hint": "全部节提交后再调用 finish_draft",
                },
                ensure_ascii=False,
            )
        # 防御性复核各节字数
        for section in self.draft.sections:
            count = count_chars(section.content)
            if section.budget > 0 and count < section.budget * _SECTION_MIN_RATIO:
                return json.dumps(
                    {
                        "status": "too_short",
                        "section": section.name,
                        "actual": count,
                        "budget": section.budget,
                        "hint": _TOO_SHORT_HINT,
                    },
                    ensure_ascii=False,
                )
        total = self.draft.total_chars()
        if total < self.target_chars * _TOTAL_MIN_RATIO:
            return json.dumps(
                {
                    "status": "too_short",
                    "total": total,
                    "target": self.target_chars,
                    "sections": [
                        {"name": s.name, "chars": count_chars(s.content), "budget": s.budget}
                        for s in self.draft.sections
                    ],
                    "hint": "总字数未达标。请按 sections 明细扩写薄弱节后重新 submit_section，禁止向用户询问是否补充。",
                },
                ensure_ascii=False,
            )
        self.draft.build_markdown()
        errors = self.draft.citation_errors()
        if errors:
            return json.dumps(
                {
                    "status": "citation_error",
                    "errors": errors,
                    "hint": "请修正正文引用；未引用的文献可用 remove_papers 移除",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "ok",
                "total_chars": total,
                "sections": [
                    {"name": s.name, "chars": count_chars(s.content), "budget": s.budget}
                    for s in self.draft.sections
                ],
            },
            ensure_ascii=False,
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
        """把历史消息中旧分节正文替换为占位符，避免多轮修改时上下文膨胀。"""
        for msg in self.messages:
            if msg.get("role") != "assistant":
                continue
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                if (
                    fn.get("name") != "submit_section"
                    or len(fn.get("arguments", "")) <= _SECTION_FOLD_THRESHOLD
                ):
                    continue
                try:
                    args = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    continue
                # 保留 name 键，历史里才能看出是哪节
                fn["arguments"] = json.dumps(
                    {
                        "name": str(args.get("name", "")),
                        "content": "（旧节内容已折叠，以最近一次提交为准）",
                    },
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
        name = _sanitize_filename(self.draft.title or "未命名论文")
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
