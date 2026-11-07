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
            "name": "search_literature",
            "description": "在多个学术数据源（arXiv、OpenAlex、Semantic Scholar，"
            "以及用户外接的本地文献库，若有）检索真实文献，"
            "已跨源去重并按权威度排序合并。默认登记入文献库并返回引用编号（起草引用以此为准）；"
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
            "name": "set_target_chars",
            "description": "设置全文目标字数，影响大纲预算校验与 finish_draft 的总字数门禁。"
            "用户对全文篇幅提出要求时调用，如「正文 8000 字左右」「写篇 1.5 万字的」"
            "「帮我把字数改成 6000」「来篇五千词的论文」；「1.5 万」须换算为 15000。"
            "起草请求自带字数要求（如「写篇 8000 字的论文」）时，必须先调用本工具设定目标，"
            "再进入检索与起草，保证大纲预算与总字数门禁按该目标校验。"
            "用户只描述局部篇幅（如「摘要 300 字」「每节 800 字」「第 3 节扩到 1200 字」）"
            "或未提字数时不调用；各节篇幅分配用 begin_draft 的 sections 预算表达，不要用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_chars": {
                        "type": "integer",
                        "description": "目标字数（整数），「N 万字」换算为 N*10000，最低 500",
                    },
                },
                "required": ["target_chars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": "系统性文献调研：派出调研子代理围绕课题多角度检索真实文献，返回资料卡"
            "（编号、标题、核心要点），资料卡会存入会话状态供写作子代理引用。"
            "论文起草前调用；写作过程中需要补充某方面文献时也可再次调用。"
            "单次快查或咨询（结果不入库、不返回编号）仍用 search_literature 传 register=false。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "调研课题，一句话说清研究问题，如 '大语言模型幻觉的缓解方法'",
                    },
                },
                "required": ["topic"],
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
            "证据不足时自动降级——同指标跨数据集参考或低置信区间，返回的 tier 字段标明证据强度"
            "（exact/cross_dataset/sparse）；仅 insufficient 或 no_match 时该单元格改用【待补充：……】。",
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
            "用户未给出明确研究主题时不得调用本工具，先向用户追问。"
            "提交成功后提纲进入待确认状态：须向用户展示提纲并请求确认，"
            "用户确认后调用 confirm_outline 解锁写作；未确认前 submit_section 会被拒绝。",
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
            "name": "confirm_outline",
            "description": "提纲经用户确认后调用，解锁逐节写作（submit_section）。"
            "仅在用户对提纲明确表态后调用：「可以」「没问题，开始写」视为确认；"
            "用户要求修改时按意见调整并重新 begin_draft，再次请求确认；"
            "用户明确要求跳过确认直接写时也可调用。"
            "提纲提交后的同一轮内用户尚未回应，调用会被拒绝。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_section",
            "description": "派出写作子代理撰写或重写指定一节：子代理在独立上下文中按字数预算写作、"
            "自行校验字数并把正文写入草稿，主对话只返回该节的内容摘要（digest）。"
            "逐节起草与整节改写（含按审校意见修改）都用本工具；"
            "一两句话的局部微调才用 submit_section。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节名，与大纲中完全一致"},
                    "guidance": {
                        "type": "string",
                        "description": "可选，写作要求：侧重内容、必须覆盖的要点，"
                        "或按审校意见修改的具体条目（意见原文传入）",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_section",
            "description": "提交一节正文（Markdown 段落，不含节标题行）。"
            "name 必须与 begin_draft 中的节名完全一致；同名重复提交为覆盖修改。"
            "提纲未经用户确认（confirm_outline）前调用会被拒绝。"
            "每节字数低于预算 85% 或超出预算 130% 会被拒绝，须按预算自行调整后重交，禁止反问用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节名，与大纲中完全一致"},
                    "content": {
                        "type": "string",
                        "description": "该节正文（Markdown），可含 [n] 引用标记、Markdown 表格"
                        "（表题行'表 N：标题'紧贴表格上方）与 LaTeX 公式"
                        "（行内 $...$，独立公式整行 $$...$$，公式只能写在段落里），不含节标题行",
                    },
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_draft",
            "description": "派出审校子代理对已提交章节做整体审校，返回按严重程度分级的问题列表"
            "（结构、论证、表达与引用规范）。全部节写完后、finish_draft 前调用；"
            "返回意见需逐条处理：整节改动走 write_section 附 guidance，小改直接 submit_section。",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "可选，本次审校的关注点，如 '论证是否充分'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_draft",
            "description": "全部节提交后调用：校验各节齐全、总字数、引用一致性与比较性断言量化，拼接全文。通过后才可 evaluate_draft 或 export_docx。",
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
            "name": "evaluate_draft",
            "description": "定量评估当前草稿质量并生成基线对比分数：claim_audit（比较性断言量化度）、"
            "citation_consistency（引用一致性）、structure_balance（结构均衡）、"
            "numeric_discipline（数据纪律）、language_judge（语言质量）。"
            "finish_draft 通过后、export_docx 前调用，结果保存为 eval.json 并报告相对基线的 delta；"
            "返回 unquantified 非空时必须逐条改写为含量化支撑的表述。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_docx",
            "description": "把当前论文导出为规范排版的 Word 文档、可编译的 .tex 源码，"
            "并生成可直接交给 PPT Agent 的 ppt_outline.json 和 ppt_outline.md；不输出原稿文件。"
            "finish_draft 通过后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_guides": {
                        "type": "boolean",
                        "description": "是否附加写作指导批注（节标题写作要点、†预期值替换提醒）。"
                        "默认 true；用户明确要求不加批注时传 false。",
                    },
                },
            },
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
    {
        "type": "function",
        "function": {
            "name": "read_todo",
            "description": "读取当前任务清单（步骤 + 状态）与完成进度。"
            "接手系统提示词中标注的未完成任务（断点恢复）时先调用本工具确认进度；"
            "清单内容已在上下文中或刚更新过时无需重复调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "写入/更新任务清单（整体替换）。多步骤任务（如论文起草）开始时建立清单："
            "按执行顺序列出各步，当前步 in_progress、其余 pending；"
            "每完成一步立即更新该步为 completed 并把下一步置为 in_progress，禁止攒到最后一次性更新。"
            "状态只有三种：pending / in_progress / completed。"
            "清单会持久化到磁盘：进程中断后重启，据此从第一个未完成步骤继续，不重做已完成步骤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "完整步骤列表（整体替换现有清单），按执行顺序",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "步骤内容，动宾短语，如 '调研检索增强生成相关文献'",
                                },
                                "status": {
                                    "type": "string",
                                    "description": "该步状态：pending / in_progress / completed",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["steps"],
            },
        },
    },
]

# ---- 结果提示文案（与 schema 描述同属对模型的指令）----

# 提纲已提交，等待用户确认（硬性流程节点）
HINT_OUTLINE_PENDING = (
    "提纲已提交、尚未生效：向用户简短报告题目与各节安排并请求确认"
    "（一两句话即可，禁止整篇复述提纲或输出正文），本轮到此为止。"
    "用户确认后调用 confirm_outline 解锁写作；用户要求修改时调整后重新 begin_draft。"
    "用户未回应前禁止 submit_section，也不得自行调用 confirm_outline。"
)

# 提纲未经用户确认就提交正文
HINT_NOT_CONFIRMED = (
    "提纲尚未经用户确认。若用户已表示同意，先调用 confirm_outline 解锁写作；"
    "否则先向用户展示提纲并等待确认，禁止继续写作。"
)

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
    "返回 insufficient 或 no_match（降级估计也无法给出）时改用【待补充：……】。"
)

# 比较性断言缺少量化/引用支撑
HINT_UNQUANTIFIED = (
    "以下比较性断言没有数字、区间或引用支撑，须逐条改写：补上 estimate_metric 的具体数值与 [n] 引用，"
    "或如实弱化措辞（改为趋势描述）；改写后重新 submit_section 受影响节，再 finish_draft。"
)
