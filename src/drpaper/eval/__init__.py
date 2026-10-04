"""基线校准评测：固定标准任务集对草稿做量化评分（与 agent 循环解耦）。"""

from drpaper.eval.claims import ClaimFinding, scan_claims
from drpaper.eval.config import EvalConfig, load_baseline, load_eval_config
from drpaper.eval.scorecard import Scorecard, TaskScore
from drpaper.eval.tasks import run_evaluation

__all__ = [
    "ClaimFinding",
    "EvalConfig",
    "Scorecard",
    "TaskScore",
    "load_baseline",
    "load_eval_config",
    "run_evaluation",
    "scan_claims",
]
