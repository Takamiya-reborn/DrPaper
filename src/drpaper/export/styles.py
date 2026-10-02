"""学术 docx 格式规范常量（对齐常见期刊/学位论文要求）。"""

from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.shared import Cm, Pt

# 字体
CN_BODY_FONT = "宋体"
CN_HEADING_FONT = "黑体"
EN_FONT = "Times New Roman"

# 字号（中文字号制）
SIZE_TITLE = Pt(16)  # 三号：题目
SIZE_HEADING_1 = Pt(14)  # 四号：一级节标题
SIZE_BODY = Pt(12)  # 小四：正文/节标题/摘要
SIZE_REFERENCE = Pt(10.5)  # 五号：参考文献

# 页面
PAGE_MARGINS = Cm(2.54)

# 段落
LINE_SPACING = WD_LINE_SPACING.ONE_POINT_FIVE
FIRST_LINE_INDENT_CHARS = 2

ALIGN_JUSTIFY = WD_ALIGN_PARAGRAPH.JUSTIFY
ALIGN_CENTER = WD_ALIGN_PARAGRAPH.CENTER

# 写作指导批注（面向初学者，按节标题关键词匹配）
SECTION_GUIDES: list[tuple[str, str]] = [
    (
        "引言",
        "引言的常见脉络：研究背景与意义 → 现有方法及其不足 → 本文要解决的问题与贡献。"
        "最后一段通常概述全文结构。",
    ),
    (
        "相关工作",
        "按主题（而非按时间流水账）归纳已有研究，每组末尾指出与本文的差异，突出研究空白。",
    ),
    (
        "方法",
        "先给总体框架（可配图），再按模块逐一展开；公式和符号首次出现时务必解释含义。",
    ),
    (
        "实验",
        "说明数据集、对比方法、评价指标与实现细节；结果用表格呈现，并解释数字背后的原因。",
    ),
    (
        "结论",
        "总结贡献、承认局限、给出未来工作方向；不要引入正文未讨论过的新内容。",
    ),
    (
        "摘要",
        "摘要四要素：问题背景 → 本文方法 → 实验结果 → 意义结论，一般 200~300 字，独立成文。",
    ),
]


def guide_for_heading(heading_text: str) -> str | None:
    """根据节标题文本返回匹配到的写作指导批注，无匹配返回 None。"""
    for keyword, guide in SECTION_GUIDES:
        if keyword in heading_text:
            return guide
    return None
