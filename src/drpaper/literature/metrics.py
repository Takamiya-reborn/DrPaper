"""文献指标库：从摘要抽取实测数字，按元分析思路估计预期指标区间。

分层原则：基线数值是文献实测（quote 可溯源至摘要原句），
"本文方法"的预期区间由确定性统计从这批实测值推出——
LLM 只负责语义抽取，数字的校验与估计全部是可复现的确定性代码。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from drpaper.literature.base import Paper
from drpaper.llm.client import LLMClient

# 单次抽取调用的论文数上限（摘要较长，控制 token）
_EXTRACT_BATCH = 12

_EXTRACT_SYSTEM = """你是文献数据抽取器。从论文摘要中抽取性能数字，输出 JSON 数组：
[{"uid": "...", "dataset": "数据集名", "metric": "指标名", "method": "方法名", "value": 0.0, "quote": "包含该数字的原句"}]

规则：
- value 必须逐字取自摘要中的数字，禁止换算、四舍五入或补零
- dataset/metric/method 一律用摘要原词（英文）；摘要未提及的字段填 ""
- 一条摘要可抽出多条记录；没有性能数字则输出 []
- 只输出 JSON 数组，不要任何解释"""


@dataclass(frozen=True)
class MetricRecord:
    """一条从文献摘要中抽出的实测数字（quote 用于溯源校验）。"""

    uid: str
    dataset: str
    metric: str
    method: str
    value: float
    quote: str


# ---- 抽取 ----

def extract_records(llm: LLMClient, papers: list[Paper]) -> list[MetricRecord]:
    """让 LLM 从一批摘要中抽取实测数字，并做逐字校验。

    校验不通过的记录直接丢弃——宁可漏抽，不可引入不存在的数字。
    """
    abstracts = {p.uid: p.abstract for p in papers}
    payload = [{"uid": p.uid, "title": p.title, "abstract": p.abstract} for p in papers]
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        text = llm.chat(messages, [], {})
    except Exception:  # noqa: BLE001 - 抽取失败不应中断起草，退化为无可比文献
        return []
    records: list[MetricRecord] = []
    for item in _parse_json_array(text):
        record = _verified_record(item, abstracts)
        if record:
            records.append(record)
    return records


def _parse_json_array(text: str) -> list:
    """解析 LLM 输出中的 JSON 数组，容忍代码块包裹；解析失败返回空。"""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _verified_record(item: object, abstracts: dict[str, str]) -> MetricRecord | None:
    """单条记录校验：quote 必须是摘要原文，value 必须逐字出现在 quote 中。"""
    if not isinstance(item, dict):
        return None
    abstract = abstracts.get(str(item.get("uid", "")))
    dataset = str(item.get("dataset", "")).strip()
    metric = str(item.get("metric", "")).strip()
    quote = " ".join(str(item.get("quote", "")).split())
    if not abstract or not dataset or not metric or not quote:
        return None
    if quote not in " ".join(abstract.split()):
        return None
    try:
        value = float(item.get("value"))
    except (TypeError, ValueError):
        return None
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", quote)]
    if not any(abs(n - value) < 1e-9 for n in numbers):
        return None
    return MetricRecord(
        uid=str(item.get("uid", "")),
        dataset=dataset,
        metric=metric,
        method=str(item.get("method", "")).strip() or "未注明",
        value=value,
        quote=quote,
    )


class MetricStore:
    """会话内指标库：按论文缓存抽取结果（uid → 记录列表，空列表表示已抽过但无数字）。"""

    def __init__(self) -> None:
        self._records: dict[str, list[MetricRecord]] = {}

    def ensure_extracted(self, llm: LLMClient, papers: list[Paper]) -> None:
        """补齐未抽取过的论文；每篇只抽一次。"""
        pending = [p for p in papers if p.uid not in self._records]
        for paper in pending:
            self._records[paper.uid] = []
        for start in range(0, len(pending), _EXTRACT_BATCH):
            batch = pending[start : start + _EXTRACT_BATCH]
            for record in extract_records(llm, batch):
                self._records[record.uid].append(record)

    def match(self, dataset: str, metric: str) -> tuple[list[MetricRecord], list[str]]:
        """按数据集/指标模糊匹配记录；无匹配时返回现有组合供模型纠正叫法。"""
        all_records = [r for records in self._records.values() for r in records]
        matched = [
            r
            for r in all_records
            if _fuzzy(r.dataset, dataset) and (not metric or _fuzzy(r.metric, metric))
        ]
        combos = sorted({f"{r.dataset} / {r.metric}" for r in all_records})
        return matched, combos


def _fuzzy(a: str, b: str) -> bool:
    """归一化后双向子串匹配，容忍大小写与标点差异（SST2 ≈ SST-2）。"""
    key_a, key_b = _norm_key(a), _norm_key(b)
    return bool(key_a and key_b) and (key_a in key_b or key_b in key_a)


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", text.lower())


# ---- 估计 ----

def estimate(
    records: list[MetricRecord],
    dataset: str,
    metric: str,
    higher_is_better: bool = True,
) -> dict:
    """从同数据集同指标的实测值做统计估计，产出预期区间与可直接入表的 cell 字符串。

    规则：锚定最优基线，区间宽度取自文献间散布（四分位距）；
    文献分歧大时加宽（异质性调整）；下界刻意允许低于最优基线，
    对冲文献报告普遍偏乐观的发表偏倚。实测值不足 3 条拒绝估计。
    """
    values = sorted(r.value for r in records)
    if len(values) < 3:
        return {"status": "insufficient", "n": len(values), "dataset": dataset, "metric": metric}
    median = _quantile(values, 0.5)
    best = values[-1] if higher_is_better else values[0]
    spread = _quantile(values, 0.75) - _quantile(values, 0.25)
    if spread <= 1e-12:
        spread = max(0.03 * abs(median), 1e-3)
    heterogeneous = spread > 0.10 * max(abs(median), 1e-9)
    low_frac, high_frac = (0.20, 0.60) if heterogeneous else (0.10, 0.40)
    if higher_is_better:
        low, high = best - low_frac * spread, best + high_frac * spread
    else:
        low, high = best - high_frac * spread, best + low_frac * spread
    # 区间宽度夹在最优值的 0.3%~5%，避免退化为单点或离谱的宽区间
    if best:
        width = min(max(high - low, 0.003 * abs(best)), 0.05 * abs(best))
        if higher_is_better:
            high = low + width
        else:
            low = high - width
    low_rounded = math.floor(low * 10) / 10
    high_rounded = math.ceil(high * 10) / 10
    if high_rounded - low_rounded < 0.2:
        high_rounded = low_rounded + 0.2
    note = (
        f"基于文献库 {len(values)} 条 {dataset}/{metric} 实测值统计，最优 {best}；"
        + ("文献间分歧较大，区间已加宽；" if heterogeneous else "")
        + "下界刻意允许略低于最优基线——文献报告的提升普遍偏乐观"
    )
    return {
        "status": "ok",
        "dataset": dataset,
        "metric": metric,
        "n": len(values),
        "best": best,
        "heterogeneous": heterogeneous,
        "cell": f"{low_rounded:.1f}~{high_rounded:.1f}†",
        "note": note,
    }


def _quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数，输入须已升序。"""
    pos = (len(sorted_values) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (pos - lower)


# ---- 校验 ----

# 预期区间单元格：如 "83.5~85.2†"，允许后跟引用标记 [n]
_PREDICTED_CELL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[~～]\s*(\d+(?:\.\d+)?)\s*†")


def expected_cell_errors(cells: list[str], issued_cells: list[str]) -> list[str]:
    """校验表格中所有†单元格都原样来自 estimate_metric 的返回，返回问题描述列表。"""
    issued = set()
    for cell in issued_cells:
        match = _PREDICTED_CELL.match(cell)
        if match:
            issued.add(_cell_key(match))
    errors: list[str] = []
    for cell in cells:
        if "†" not in cell:
            continue
        match = _PREDICTED_CELL.match(cell)
        if match is None:
            errors.append(
                f"单元格「{cell.strip()}」含†但不是区间格式，"
                "应原样使用 estimate_metric 返回的 cell 字符串（如 83.5~85.2†）"
            )
        elif _cell_key(match) not in issued:
            errors.append(
                f"预期区间「{cell.strip()}」不在 estimate_metric 的返回结果内，禁止改动数字"
            )
    return errors


def _cell_key(match: re.Match) -> tuple[float, float]:
    return (round(float(match.group(1)), 4), round(float(match.group(2)), 4))
