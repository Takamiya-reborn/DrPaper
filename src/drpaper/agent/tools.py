"""Agent 工具：把文献检索、分节起草与 docx 导出包装成 LLM 可调用的工具。

ToolBox 只做参数解析与结果装配，具体能力分层委托：
文献呈现 literature.present、字数与大纲校验 paper.draft、
历史折叠 agent.history、文件命名 export.naming、skill 去重 skills.manager。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from drpaper.agent.history import fold_old_section_drafts
from drpaper.agent.schemas import (
    HINT_CITATION,
    HINT_INCOMPLETE,
    HINT_TOO_LONG,
    HINT_TOO_SHORT,
    HINT_TOTAL_SHORT,
)
from drpaper.export.docx_writer import export_docx
from drpaper.export.naming import sanitize_filename
from drpaper.export.style_profile import StyleProfile
from drpaper.literature.base import SearchProvider
from drpaper.literature.present import paper_card
from drpaper.paper.draft import Draft, parse_outline
from drpaper.paper.markdown_parser import extract_citations, parse
from drpaper.paper.wordcount import count_chars
from drpaper.skills.manager import SkillManager


def _result(**fields: Any) -> str:
    """装配给模型看的 JSON 结果。"""
    return json.dumps(fields, ensure_ascii=False)


def _error(message: str, **fields: Any) -> str:
    return _result(error=message, **fields)


@dataclass
class ToolBox:
    """持有会话状态（草稿、检索源、输出目录、消息历史）的工具集合。"""

    provider: SearchProvider
    draft: Draft
    output_dir: str
    style: StyleProfile
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

    # ---- 文献检索 ----

    def _search_arxiv(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return _error("query 不能为空")
        max_results = min(int(args.get("max_results", 5)), 10)
        register = args.get("register", True) is not False
        # 已入库的文献只回编号，避免重复检索把完整记录再灌一遍历史
        known = {p.uid for p in self.draft.references} if register else set()
        papers = self.provider.search(query, max_results)
        numbers = self.draft.add_papers(papers) if register else None
        results = []
        for paper in papers:
            if paper.uid in known:
                results.append(
                    {
                        "index": numbers[paper.uid],
                        "title": paper.title,
                        "in_library": True,
                    }
                )
                continue
            record = paper_card(paper, include_url=not register)
            if register:
                record["index"] = numbers[paper.uid]
            results.append(record)
        return _result(query=query, results=results)

    # ---- 起草 ----

    def _begin_draft(self, args: dict[str, Any]) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            return _error("title 不能为空")
        try:
            sections = parse_outline(args.get("sections"))
        except ValueError as exc:
            return _error(str(exc))
        self.draft.begin(title, sections)
        fields: dict[str, Any] = {
            "status": "ok",
            "sections": [{"name": s.name, "budget": s.budget} for s in sections],
            "target_chars": self.target_chars,
        }
        total_budget = sum(s.budget for s in sections)
        deviation = abs(total_budget - self.target_chars) / max(self.target_chars, 1)
        if deviation > 0.2:
            fields["warning"] = (
                f"各节预算之和 {total_budget} 与目标字数 {self.target_chars} 偏差较大，请调整大纲"
            )
        return _result(**fields)

    def _submit_section(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        content = str(args.get("content", "")).strip()
        if not content:
            return _error("content 不能为空")
        try:
            section = self.draft.get_section(name)
        except KeyError as exc:
            return _error(str(exc), missing=self.draft.missing_sections())
        if section.too_short(content):
            return _result(
                status="too_short",
                section=name,
                actual=count_chars(content),
                budget=section.budget,
                hint=HINT_TOO_SHORT,
            )
        if section.too_long(content):
            return _result(
                status="too_long",
                section=name,
                actual=count_chars(content),
                budget=section.budget,
                hint=HINT_TOO_LONG,
            )
        section.content = content
        fold_old_section_drafts(self.messages, name)
        fields: dict[str, Any] = {
            "status": "ok",
            "section": name,
            "chars": count_chars(content),
            "budget": section.budget,
            "progress": self._progress(),
        }
        total_refs = len(self.draft.references)
        over = sorted(n for n in extract_citations(content) if n < 1 or n > total_refs)
        if over:
            fields["warning"] = (
                f"引用编号超出文献库范围: {over}（当前共 {total_refs} 条）"
            )
        return _result(**fields)

    def _finish_draft(self, _args: dict[str, Any]) -> str:
        missing = self.draft.missing_sections()
        if missing:
            return _result(status="incomplete", missing=missing, hint=HINT_INCOMPLETE)
        # 防御性复核各节字数
        shorts = self.draft.short_sections()
        if shorts:
            section = shorts[0]
            return _result(
                status="too_short",
                section=section.name,
                actual=count_chars(section.content),
                budget=section.budget,
                hint=HINT_TOO_SHORT,
            )
        longs = self.draft.long_sections()
        if longs:
            section = longs[0]
            return _result(
                status="too_long",
                section=section.name,
                actual=count_chars(section.content),
                budget=section.budget,
                hint=HINT_TOO_LONG,
            )
        total = self.draft.total_chars()
        if self.draft.total_too_short(self.target_chars):
            return _result(
                status="too_short",
                total=total,
                target=self.target_chars,
                sections=self._sections_detail(),
                hint=HINT_TOTAL_SHORT,
            )
        self.draft.build_markdown()
        errors = self.draft.citation_errors()
        if errors:
            return _result(status="citation_error", errors=errors, hint=HINT_CITATION)
        return _result(status="ok", total_chars=total, sections=self._sections_detail())

    def _remove_papers(self, args: dict[str, Any]) -> str:
        raw = args.get("indices", [])
        try:
            indices = [int(n) for n in raw]
        except (TypeError, ValueError):
            return _error("indices 必须是整数列表")
        return _result(result=self.draft.remove_papers(indices))

    # ---- 导出 ----

    def _export_docx(self, _args: dict[str, Any]) -> str:
        errors = self.draft.citation_errors()
        if errors:
            return _result(status="blocked", errors=errors, hint=HINT_CITATION)
        name = sanitize_filename(self.draft.title or "未命名论文")
        out_dir = Path(self.output_dir) / name
        nodes = parse(self.draft.markdown)
        docx_path = out_dir / f"{name}.docx"
        export_docx(nodes, self.draft.references, docx_path, profile=self.style)
        saved = self.draft.save(out_dir)
        return _result(
            status="ok",
            docx=str(docx_path),
            related_files=[str(p) for p in saved],
            references=len(self.draft.references),
        )

    # ---- skill 加载 ----

    def _load_skill(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        file = args.get("file")
        try:
            return self.skills.load_once(name, str(file) if file else None)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            return _error(str(exc))

    # ---- 内部装配 ----

    def _progress(self) -> str:
        missing = self.draft.missing_sections()
        done = len(self.draft.sections) - len(missing)
        progress = f"已完成 {done}/{len(self.draft.sections)} 节"
        if not missing:
            return progress
        # 写下一节前把预算送到眼前，比在提示词里重复字数更省也更醒目
        upcoming = self.draft.get_section(missing[0])
        budget = f"预算 {upcoming.budget} 字" if upcoming.budget else "不限字数"
        return f"{progress}；下一节「{upcoming.name}」{budget}"

    def _sections_detail(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "chars": count_chars(s.content), "budget": s.budget}
            for s in self.draft.sections
        ]
