"""评分卡：逐任务得分、加权总分、基线差值与 JSON 落盘。

固定 eval_version 保证跨跑可比：schema 变更时必须递增。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

EVAL_VERSION = 1


@dataclass
class TaskScore:
    """单个评测任务的结果：score 为 None 表示跳过（不计入总分）。"""

    task: str
    score: float | None
    weight: float
    detail: dict = field(default_factory=dict)


@dataclass
class Scorecard:
    """一次评估的完整结果：任务列表 + 基线分数。"""

    tasks: list[TaskScore]
    baseline: dict[str, float] = field(default_factory=dict)
    version: int = EVAL_VERSION

    @property
    def total(self) -> float | None:
        """有效任务权重重归一后的加权总分；全部跳过时为 None。"""
        return _weighted_total([(t.task, t.score, t.weight) for t in self.tasks])

    @property
    def baseline_total(self) -> float | None:
        """基线的同口径加权总分（只取本次任务集内有基线的项）。"""
        entries = [
            (t.task, self.baseline.get(t.task), t.weight)
            for t in self.tasks
            if t.task in self.baseline
        ]
        return _weighted_total(entries)

    @property
    def delta(self) -> float | None:
        """总分相对基线的差值；总分或基线缺失时为 None。"""
        total, baseline_total = self.total, self.baseline_total
        if total is None or baseline_total is None:
            return None
        return total - baseline_total

    def deltas(self) -> dict[str, float | None]:
        """逐任务差值：score − baseline，跳过或无基线时为 None。"""
        return {
            t.task: (
                t.score - self.baseline[t.task]
                if t.score is not None and t.task in self.baseline
                else None
            )
            for t in self.tasks
        }

    def to_dict(self) -> dict:
        """JSON 可序列化结构（eval.json 与工具返回共用）。"""
        total, baseline_total, delta = self.total, self.baseline_total, self.delta
        task_deltas = self.deltas()
        return {
            "eval_version": self.version,
            "total": _round(total),
            "baseline_total": _round(baseline_total),
            "delta": _round(delta),
            "tasks": [
                {
                    "task": t.task,
                    "score": _round(t.score),
                    "baseline": self.baseline.get(t.task),
                    "delta": _round(task_deltas[t.task]),
                    "weight": t.weight,
                    "detail": t.detail,
                }
                for t in self.tasks
            ],
        }

    def save(self, path: Path) -> Path:
        """写入 eval.json，返回落盘路径。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def _weighted_total(entries: list[tuple[str, float | None, float]]) -> float | None:
    """加权总分：score 为 None 的项跳过，权重按有效项重归一；权重和退化为 0 时取等权均值。"""
    valid = [(score, weight) for _, score, weight in entries if score is not None]
    if not valid:
        return None
    weight_sum = sum(max(weight, 0.0) for _, weight in valid)
    if weight_sum <= 0:
        return sum(score for score, _ in valid) / len(valid)
    return sum(score * max(weight, 0.0) for score, weight in valid) / weight_sum


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None
