"""检索结果呈现：把 Paper 裁剪为适合进入对话历史的紧凑记录。"""

from __future__ import annotations

from drpaper.literature.base import Paper

# 摘要最大长度（字符）：足够判断相关性，避免完整摘要常驻历史
_ABSTRACT_MAX_CHARS = 400

# 保留的作者人数上限，超出折叠为 et al.
_AUTHOR_LIMIT = 3


def _truncate(text: str, limit: int) -> str:
    """截断过长文本，超限时追加省略标记。"""
    return text if len(text) <= limit else text[:limit].rstrip() + "……"


def paper_card(paper: Paper, include_url: bool = False) -> dict:
    """一篇文献的紧凑展示：标题、前几位作者、年份与截断摘要。

    include_url 控制是否附原文链接：咨询调研场景需要；
    起草场景的参考文献由导出时按文献库生成，无需进上下文。
    """
    record = {
        "title": paper.title,
        "authors": paper.authors[:_AUTHOR_LIMIT]
        + (["et al."] if len(paper.authors) > _AUTHOR_LIMIT else []),
        "year": paper.year,
        "abstract": _truncate(paper.abstract, _ABSTRACT_MAX_CHARS),
    }
    if include_url:
        record["url"] = paper.url
    if paper.citations > 0:
        record["citations"] = paper.citations  # 权威度信号，供模型取舍
    return record
