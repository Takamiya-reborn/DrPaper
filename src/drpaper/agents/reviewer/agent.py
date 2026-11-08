"""审校子代理：对已提交章节做整体审校，只向主循环返回意见列表。

草稿全文与文献条目直接随任务注入（子代理上下文本就独立，整篇注入
不占主对话），子代理只需一个 submit_review 工具交回按严重程度
分级的意见列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drpaper.agents.base import Subagent, error, result
from drpaper.llm.client import LLMClient
from drpaper.llm.types import ChatCallbacks, ContextCompactor, Tool

SYSTEM = """你是论文审校专家，对一篇论文的已提交章节做整体审校。

- 逐节检查：结构完整性、论证链条、章节间衔接与重复、表达与术语一致性、引用规范（[n] 是否支撑对应表述）
- 意见按严重程度分级：high=论证或事实问题，medium=结构与表达问题，low=润色建议
- 每条意见指明所在节，给出具体可执行的修改建议；不复述原文，不改写正文
- 完成后调用 submit_review 提交意见列表；工具轮次不输出叙述文字
"""

_TOOLS: list[Tool] = [
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "提交审校意见列表，结束审校任务。没有问题的方面不必凑条目；"
            "确实无需修改时 findings 传空列表并在 summary 说明。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "两三句话的总体评价：稿件当前最薄弱的环节",
                    },
                    "issues": {
                        "type": "array",
                        "description": "审校意见列表，按严重程度从高到低排序",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {
                                    "type": "string",
                                    "description": "严重程度：high / medium / low",
                                    "enum": ["high", "medium", "low"],
                                },
                                "section": {
                                    "type": "string",
                                    "description": "所在节名，与任务中列出的节名一致",
                                },
                                "problem": {
                                    "type": "string",
                                    "description": "问题描述，具体到段落或表述",
                                },
                                "suggestion": {
                                    "type": "string",
                                    "description": "具体可执行的修改建议",
                                },
                            },
                            "required": [
                                "severity",
                                "section",
                                "problem",
                                "suggestion",
                            ],
                        },
                    },
                },
                "required": ["summary", "issues"],
            },
        },
    },
]


@dataclass
class ReviewerAgent:
    """审校子代理：草稿全文随任务注入，只交回意见列表。"""

    llm: LLMClient
    max_rounds: int = 6
    compactor: ContextCompactor | None = None
    _submitted: dict[str, Any] = field(default_factory=dict, init=False)

    def run(self, task: str, callbacks: ChatCallbacks | None = None) -> dict[str, Any]:
        """执行审校 loop，返回 {status, summary, issues} 或 {status, reason}。"""
        self._submitted = {}

        def _submit(args: dict[str, Any]) -> str:
            summary = str(args.get("summary", "")).strip()
            raw = args.get("issues")
            if not summary:
                return error("summary 不能为空")
            if not isinstance(raw, list):
                return error("issues 必须是列表（无问题时传空列表）")
            issues: list[dict[str, Any]] = []
            for item in raw:
                cleaned = {
                    key: str(item.get(key, "")).strip()
                    for key in ("severity", "section", "problem", "suggestion")
                }
                if cleaned["severity"] not in {"high", "medium", "low"}:
                    return error("每条意见的 severity 必须是 high / medium / low")
                if not all(cleaned.values()):
                    return error(
                        "每条意见都必须有 severity、section、problem 与 suggestion"
                    )
                issues.append(cleaned)
            self._submitted = {"summary": summary, "issues": issues}
            return result(status="ok", note="意见列表已提交，审校结束")

        agent = Subagent(
            self.llm,
            SYSTEM,
            _TOOLS,
            {"submit_review": _submit},
            max_rounds=self.max_rounds,
            compactor=self.compactor,
        )
        reply = agent.run(task, callbacks)
        if not self._submitted:
            return {
                "status": "failed",
                "reason": f"审校子代理未提交意见列表（最终回复：{reply[:200]}）",
            }
        return {"status": "ok", **self._submitted}
