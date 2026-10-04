"""字数统计：中文论文习惯口径（对标 Word 字数统计）。"""

from __future__ import annotations

import re

from drpaper.paper.markdown_parser import CITATION_PATTERN

# 行首结构符：标题、列表、引用块
_LEADING_MARKS = re.compile(r"^(?:#{1,6}|[-*+>]|\d+[.、])\s*", re.MULTILINE)

# 行内强调符
_INLINE_MARKS = re.compile(r"[*`]")

# CJK 字符：汉字 + 中文标点 + 全角符号（中文标点计字是中文统计惯例）
_CJK_PATTERN = re.compile(
    r"[㐀-䶿一-鿿　-〿＀-￯‘’“”]"
)

# 连续英文单词/数字串，每串计 1
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def count_chars(text: str) -> int:
    """统计正文字数：每个 CJK 字符（含中文标点/全角）计 1，英文单词/数字串每串计 1。

    Markdown 结构符、强调符与引用标记不计入。
    """
    text = _LEADING_MARKS.sub("", text)
    text = _INLINE_MARKS.sub("", text)
    text = CITATION_PATTERN.sub("", text)  # 引用标记先删除，避免其中的数字被计成词
    return len(_CJK_PATTERN.findall(text)) + len(_WORD_PATTERN.findall(text))
