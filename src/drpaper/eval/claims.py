"""主张量化审计：扫描比较性/程度性断言并判定量化支撑（claim_audit 的确定性核心）。

句级判定是刻意选择：跨句判定会引入歧义且不可解释；
逐条返回原句让模型能精确定位改写。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from drpaper.paper.markdown_parser import CITATION_PATTERN

# 断言类型按严重度递减，命中即停（一句只记最重的一类）
_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("intensifier", re.compile(r"(?:显著|明显|大幅|极大)(?:提升|提高|改善|增强|降低|减少|缩短|加快)|远超")),
    ("superlative", re.compile(r"最优|最佳|最好|最先进|首(?:个|次|创)")),
    ("comparative", re.compile(r"优于|好于|强于|胜于|高于|领先于|超过|超越|不及|逊于")),
    ("fuzzy_better", re.compile(r"更加?(?:好|优|佳|高|快|准|稳|强|有效|高效|灵活|鲁棒)")),
]

# 同句内的量化支撑形态：†区间（estimate_metric 的 cell 形态）、百分比/倍数、引用标记
_INTERVAL_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*[~～]\s*\d+(?:\.\d+)?\s*†")
_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|个百分点|倍)")

# 【待补充】占位不算支撑（数据纪律允许占位，但占位句不得同时断言优劣）
_PLACEHOLDER = "【待补充"

# 语句切分：先按行（表格行天然按行隔离），行内再按句号等终止符切
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；;])")


@dataclass(frozen=True)
class ClaimFinding:
    """一条命中断言的记录：kind 为断言类型，backed_by 为量化支撑形态。"""

    section: str
    sentence: str
    kind: str  # intensifier / superlative / comparative / fuzzy_better
    backed_by: str  # interval / number / citation / none


def scan_claims(sections: list[tuple[str, str]]) -> list[ClaimFinding]:
    """逐节逐句扫描比较性/程度性断言，判定同句内的量化支撑。"""
    findings: list[ClaimFinding] = []
    for section, content in sections:
        for line in content.splitlines():
            for sentence in _SENTENCE_SPLIT.split(line):
                sentence = sentence.strip()
                if not sentence:
                    continue
                kind = _match_kind(sentence)
                if kind is None:
                    continue
                findings.append(
                    ClaimFinding(
                        section=section,
                        sentence=sentence[:60],
                        kind=kind,
                        backed_by=_backed_by(sentence),
                    )
                )
    return findings


def finding_dict(finding: ClaimFinding) -> dict:
    """转 JSON 可序列化 dict（工具返回用）。"""
    return asdict(finding)


def _match_kind(sentence: str) -> str | None:
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(sentence):
            return kind
    return None


def _backed_by(sentence: str) -> str:
    if _PLACEHOLDER in sentence:
        return "none"
    if _INTERVAL_PATTERN.search(sentence):
        return "interval"
    if _NUMBER_PATTERN.search(sentence):
        return "number"
    if CITATION_PATTERN.search(sentence):
        return "citation"
    return "none"
