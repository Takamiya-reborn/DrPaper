"""调研子代理：围绕课题多角度检索文献，只向主循环返回资料卡。

检索复用主循环的 search_literature 处理器：文献登记进共享文献库
（全文引用编号保持一致），完整检索结果留在本子代理上下文里，
主循环只拿到 submit_research 交回的资料卡（编号 + 标题 + 要点）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drpaper.agents.base import Subagent, error, result
from drpaper.llm.client import LLMClient
from drpaper.llm.types import ChatCallbacks, Tool, ToolHandler

SYSTEM = """你是文献调研员，围绕给定课题检索真实学术文献并汇总资料卡。

- 进行 2~4 组不同角度的英文检索（研究现状、主流方法、最新进展等），每组 max_results 取 5 左右
- 全部检索完成后调用 submit_research 提交资料卡：findings 覆盖检索到的主要文献，
  index 与 title 原样取自检索结果，key_point 用一两句话概括该文献的核心发现或与课题的关联
- summary 用两三句话概括调研结论：研究现状如何、哪些文献可支撑论文的哪些部分
- 不撰写论文正文；工具轮次不输出叙述文字，只在最终回复里简短确认调研完成
"""

_TOOLS: list[Tool] = [
    {
        "type": "function",
        "function": {
            "name": "search_literature",
            "description": "检索学术文献，结果自动登记入库并返回引用编号（资料卡编号以此为准）。"
            "围绕课题从不同角度多次调用，英文检索词。",
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
            "name": "submit_research",
            "description": "提交调研资料卡，结束调研任务。findings 每条对应一篇值得引用的文献："
            "index 与 title 用检索返回的原值，禁止编造编号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "调研结论（两三句话）：研究现状与可引用的支撑点",
                    },
                    "findings": {
                        "type": "array",
                        "description": "资料卡列表，按引用编号排序",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {
                                    "type": "integer",
                                    "description": "引用编号，与检索结果一致",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "文献标题，原样取自检索结果",
                                },
                                "key_point": {
                                    "type": "string",
                                    "description": "一两句话：核心发现或与课题的关联",
                                },
                            },
                            "required": ["index", "title", "key_point"],
                        },
                    },
                },
                "required": ["summary", "findings"],
            },
        },
    },
]


@dataclass
class ResearchAgent:
    """文献调研子代理：完整检索结果留在本地，只交回资料卡。"""

    llm: LLMClient
    search: ToolHandler  # 主循环的 search_literature 处理器（登记共享文献库）
    max_rounds: int = 8
    _submitted: dict[str, Any] = field(default_factory=dict, init=False)

    def run(self, topic: str, callbacks: ChatCallbacks | None = None) -> dict[str, Any]:
        """执行调研 loop，返回 {status, summary, findings} 或 {status, reason}。"""
        self._submitted = {}

        def _submit(args: dict[str, Any]) -> str:
            summary = str(args.get("summary", "")).strip()
            raw = args.get("findings")
            if not summary:
                return error("summary 不能为空")
            if not isinstance(raw, list) or not raw:
                return error("findings 不能为空：把值得引用的文献整理成资料卡再提交")
            findings: list[dict[str, Any]] = []
            for item in raw:
                try:
                    index = int(item.get("index"))
                except (TypeError, ValueError, AttributeError):
                    return error("findings 中存在非法 index（必须是整数）")
                title = str(item.get("title", "")).strip()
                key_point = str(item.get("key_point", "")).strip()
                if not title or not key_point:
                    return error("findings 每条都必须有 title 与 key_point")
                findings.append(
                    {"index": index, "title": title, "key_point": key_point}
                )
            self._submitted = {"summary": summary, "findings": findings}
            return result(status="ok", note="资料卡已提交，调研结束")

        agent = Subagent(
            self.llm,
            SYSTEM,
            _TOOLS,
            {"search_literature": self.search, "submit_research": _submit},
            max_rounds=self.max_rounds,
        )
        reply = agent.run(topic, callbacks)
        if not self._submitted:
            return {
                "status": "failed",
                "reason": f"调研子代理未提交资料卡（最终回复：{reply[:200]}）",
            }
        return {"status": "ok", **self._submitted}
