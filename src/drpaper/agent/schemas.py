"""工具契约：LLM 可见的 schema 定义与结果提示文案。

这里集中所有"对模型说的话"：工具的参数描述（TOOL_SCHEMAS）
与校验未通过时随结果返回的 hint。调整文案只改这里，工具实现不掺文案。
"""

from __future__ import annotations

from typing import Any

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
            "name": "estimate_metric",
            "description": "为实验对比表估计指标区间：从文献库摘要抽取真实实测数字做统计估计。"
            "写含性能数字的对比表前必须先调用；返回的 baselines 用于基线行（标 [n] 引用），"
            "cell 字符串原样用作「本文方法」行（含†，禁止改动数字）。"
            "返回 insufficient 或 no_match 时该单元格改用【待补充：……】。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "数据集名，与文献中的叫法一致，如 'SST-2'",
                    },
                    "metric": {
                        "type": "string",
                        "description": "指标名，如 'Accuracy'、'F1'；留空匹配任意指标",
                    },
                    "higher_is_better": {
                        "type": "boolean",
                        "description": "指标是否越大越好，默认 true（误差类指标传 false）",
                    },
                },
                "required": ["dataset", "metric"],
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
            "每节字数低于预算 85% 或超出预算 130% 会被拒绝，须按预算自行调整后重交，禁止反问用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节名，与大纲中完全一致"},
                    "content": {
                        "type": "string",
                        "description": "该节正文（Markdown），可含 [n] 引用标记与 Markdown 表格"
                        "（表题行'表 N：标题'紧贴表格上方），不含节标题行",
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

# ---- 结果提示文案（与 schema 描述同属对模型的指令）----

# 各节未提交齐全
HINT_INCOMPLETE = "全部节提交后再调用 finish_draft"

# 单节字数低于预算下限
HINT_TOO_SHORT = (
    "字数不足。请自行扩写本节（补充机制细节、实例与文献综述深度）"
    "后重新 submit_section，禁止向用户询问是否补充。"
)

# 单节字数超出预算上限
HINT_TOO_LONG = (
    "字数超出预算。请压缩本节（合并重复论述、删去冗余衔接与铺垫）"
    "后重新 submit_section，禁止向用户询问是否删减。"
)

# 全文字数低于目标字数下限
HINT_TOTAL_SHORT = (
    "总字数未达标。请按 sections 明细扩写薄弱节后重新 submit_section，"
    "禁止向用户询问是否补充。"
)

# 正文引用与文献库不一致
HINT_CITATION = "请修正正文引用；未引用的文献可用 remove_papers 移除"

# 表格中标†的预期值与估计结果不一致
HINT_EXPECTED_VALUE = (
    "表格中标†的数值必须原样使用 estimate_metric 返回的 cell 字符串（含区间与†），"
    "禁止改动数字；尚未估计的先调用 estimate_metric，"
    "返回 insufficient 或 no_match 时改用【待补充：……】。"
)
