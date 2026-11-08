"""写作子代理：在独立上下文撰写论文的单节正文，只向主循环返回内容摘要。

正文经 save_section 直接写入共享 Draft（预算校验不过就在本子代理
loop 里自行调整重交），主循环只拿到节名、字数与 digest 摘要——
整节文本不进主对话，这是主上下文省 token 的关键。
同一份系统提示词对每一节完全一致，配合前文摘要注入保证各章风格统一。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drpaper.agent.schemas import HINT_TOO_LONG, HINT_TOO_SHORT, TOOL_SCHEMAS
from drpaper.agents.base import Subagent, error, result
from drpaper.literature.present import paper_card
from drpaper.llm.client import LLMClient
from drpaper.llm.types import ChatCallbacks, ContextCompactor, Tool, ToolHandler
from drpaper.paper.draft import Draft
from drpaper.paper.markdown_parser import extract_citations
from drpaper.paper.wordcount import count_chars

SYSTEM = """你是中文学术论文写作专家，负责撰写一篇论文中的指定一节。

## 职责
- 只写分配给你的那一节；任务中给出论文题目、大纲、已完成各节的摘要与研究资料卡
- 承接已完成各节的内容与行文风格，禁止重复其内容；术语译名与全文保持一致，术语首次出现附英文原词
- 写完调用 save_section 提交：content 为该节 Markdown 正文（不含节标题行），digest 用一两句话概括本节内容

## 铁律
- 只引用文献库中的文献：编号 [1]、[2,3] 必须与资料卡及 read_reference 返回的编号一致，禁止编造；需要某条文献的细节时调用 read_reference
- **数据纪律**：正文中的实验数字只有两个来源——estimate_metric 返回的 baselines（基线实测值，标 [n] 引用）与其 cell 字符串（本文方法预期区间，含†）；禁止编造、改动或自行计算任何数值；返回 tier 非 exact 时如实写明证据强度与来源；返回 insufficient 或 no_match 时该处用【待补充：……】占位。
- **量化纪律**：比较性断言（优于/显著提升/更好/远超…）必须带数字、†区间或 [n] 引用支撑，禁止无依据的程度性表述
- **数据呈现**：写含性能数字的对比表前必须先调用 estimate_metric；多列对比数据必须用 Markdown 表格呈现，表题行格式为"表 N：标题"，紧贴表格首行上方；基线行引用返回的 baselines 并标 [n]，本文方法行原样填入 cell 字符串（含†）；表格后必须紧跟一段分析文字，说明数字背后的原因并与方法机制建立联系，对†区间用"预期/有望/参考"措辞，禁止"实验结果表明"
- **字数纪律**：低于预算 85% 或超出预算 130% 时 save_section 会拒绝，按返回提示自行扩写或压缩后重新提交，禁止向用户提问
- **数学公式**：公式一律用 LaTeX 语法——行内公式写 $...$，独立公式整行写 $$...$$；公式只出现在正文段落中，表格单元格、节标题与表题里禁止写公式；公式内符号一律用 LaTeX 命令（如 \\frac{a}{b}、\\sum_{i=1}^{n}、\\alpha），禁止用 Unicode 数学符号凑公式
- 工具轮次不输出叙述文字；正文不要输出到对话里，只通过 save_section 提交
"""

# estimate_metric 的工具契约与主循环完全一致，直接复用 schema
_ESTIMATE_SCHEMA = next(
    t for t in TOOL_SCHEMAS if t["function"]["name"] == "estimate_metric"
)

_TOOLS: list[Tool] = [
    {
        "type": "function",
        "function": {
            "name": "read_reference",
            "description": "查看文献库中某条文献的详情（标题、作者、年份、摘要）。"
            "写作需要文献细节支撑时调用，编号与资料卡一致。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "引用编号，从 1 开始"},
                },
                "required": ["index"],
            },
        },
    },
    _ESTIMATE_SCHEMA,
    {
        "type": "function",
        "function": {
            "name": "save_section",
            "description": "提交分配给你的那一节正文并写入草稿，提交后任务完成。"
            "content 不含节标题行；digest 用一两句话概括本节内容（主对话靠它衔接全文）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节名，与任务分配的一致"},
                    "content": {
                        "type": "string",
                        "description": "该节正文（Markdown），可含 [n] 引用标记、Markdown 表格"
                        "（表题行紧贴表格上方）与 LaTeX 公式（只写在段落里），不含节标题行",
                    },
                    "digest": {
                        "type": "string",
                        "description": "一两句话概括本节内容，供后续各节承接衔接",
                    },
                },
                "required": ["name", "content", "digest"],
            },
        },
    },
]


@dataclass
class WriterAgent:
    """单节写作子代理：正文写入共享 Draft，主循环只拿摘要。"""

    llm: LLMClient
    draft: Draft
    estimate_metric: (
        ToolHandler  # 主循环的指标估计处理器（共享 metrics 与已发单元格记录）
    )
    section_name: str  # 分配给本次写作的节名
    max_rounds: int = 14
    compactor: ContextCompactor | None = None
    _saved: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # 主循环已按大纲预检过节名，这里取不到直接上抛
        self._section = self.draft.get_section(self.section_name)

    def run(self, task: str, callbacks: ChatCallbacks | None = None) -> dict[str, Any]:
        """执行写作 loop，返回 {status, section, chars, budget, digest} 或 {status, reason}。"""
        self._saved = {}
        section = self._section

        def _read_reference(args: dict[str, Any]) -> str:
            try:
                index = int(args.get("index"))
            except (TypeError, ValueError):
                return error("index 必须是整数")
            if not 1 <= index <= len(self.draft.references):
                return error(f"编号超出文献库范围（1~{len(self.draft.references)}）")
            paper = self.draft.references[index - 1]
            return result(index=index, **paper_card(paper))

        def _save(args: dict[str, Any]) -> str:
            name = str(args.get("name", "")).strip()
            if name != self.section_name:
                return error(
                    f"只能提交分配给你的节「{self.section_name}」，收到的是「{name}」"
                )
            content = str(args.get("content", "")).strip()
            if not content:
                return error("content 不能为空")
            digest = str(args.get("digest", "")).strip()
            if not digest:
                return error("digest 不能为空：用一两句话概括本节内容")
            if section.too_short(content):
                return result(
                    status="too_short",
                    actual=count_chars(content),
                    budget=section.budget,
                    hint=HINT_TOO_SHORT,
                )
            if section.too_long(content):
                return result(
                    status="too_long",
                    actual=count_chars(content),
                    budget=section.budget,
                    hint=HINT_TOO_LONG,
                )
            section.content = content
            self._saved = {
                "chars": count_chars(content),
                "budget": section.budget,
                "digest": digest,
            }
            total = len(self.draft.references)
            over = sorted(n for n in extract_citations(content) if n < 1 or n > total)
            if over:
                self._saved["warning"] = (
                    f"引用编号超出文献库范围: {over}（当前共 {total} 条），请修正后重新提交"
                )
            return result(**{**self._saved, "section": name})

        agent = Subagent(
            self.llm,
            SYSTEM,
            _TOOLS,
            {
                "read_reference": _read_reference,
                "estimate_metric": self.estimate_metric,
                "save_section": _save,
            },
            max_rounds=self.max_rounds,
            compactor=self.compactor,
        )
        reply = agent.run(task, callbacks)
        if not self._saved:
            return {
                "status": "failed",
                "reason": f"写作子代理未提交正文（最终回复：{reply[:200]}）",
            }
        return {"status": "ok", **self._saved}
