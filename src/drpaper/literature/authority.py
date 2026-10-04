"""权威度评分：由引用数、发表载体与年份近新度计算确定性分数。

纯函数、无 LLM、无网络：给聚合器做合并排序与降级判断用。
arXiv 文献没有引用数（citations=0），仍有载体与近新度项，保证同源排序有意义。
"""

from __future__ import annotations

import math
from datetime import datetime

from drpaper.literature.base import Paper

# 引用数对数项权重：log10(1+citations) × 该系数
_CITATION_SCALE = 2.0

# 有期刊/会议信息（非纯预印本）加成
_VENUE_BONUS = 0.5

# 近 3 年发表加成：预印本与新工作更贴近前沿
_FRESH_BONUS = 1.0

# 近 6 年发表加成（与 _FRESH_BONUS 不叠加）
_RECENT_BONUS = 0.5

# 判定"新/近"文献的年份窗口
_FRESH_YEARS = 3
_RECENT_YEARS = 6


def authority_score(paper: Paper, source_weight: float = 1.0) -> float:
    """authority = (log10(1+引用数)×2 + 载体加成 + 近新度加成) × 源权重。"""
    score = math.log10(1 + max(paper.citations, 0)) * _CITATION_SCALE
    if paper.venue:
        score += _VENUE_BONUS
    score += _recency_bonus(paper.year)
    return score * source_weight


def _recency_bonus(year: int) -> float:
    """按发表年份给近新度加成：3 年内 > 6 年内 > 更早。"""
    if year <= 0:
        return 0.0
    current = datetime.now().year
    if current - year <= _FRESH_YEARS:
        return _FRESH_BONUS
    if current - year <= _RECENT_YEARS:
        return _RECENT_BONUS
    return 0.0
