"""评估配置：内置 + 用户两级 eval.yaml / baseline.yaml 加载（sources_config 同款模式）。

- 内置 YAML 随包分发（打包时由 package.spec 带入）
- 用户文件：源码运行取 ~/.drpaper/eval.yaml，打包运行取 exe 同级 eval.yaml，浅合并覆盖
- 与 sources_config 的两处刻意偏离：
  - 校验失败抛 ValueError 而非 SystemExit：本配置可能在工具调用内触发，
    SystemExit 会穿透 LLM 循环的 except Exception 直接崩掉会话
  - 未知键忽略、权重和不校验（计分时按有效任务重归一）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from drpaper.runtime import user_resource_path

BUILTIN_EVAL_PATH = Path(__file__).parent / "eval.yaml"
BUILTIN_BASELINE_PATH = Path(__file__).parent / "baseline.yaml"

# 各任务默认权重（eval.yaml 的 weights 段可覆盖同名字段）
_DEFAULT_WEIGHTS = {
    "claim_audit": 0.35,
    "citation_consistency": 0.20,
    "numeric_discipline": 0.20,
    "structure_balance": 0.10,
    "language_judge": 0.15,
}


@dataclass(frozen=True)
class EvalConfig:
    """评估参数：任务权重与各任务的阈值/扣分。"""

    weights: dict[str, float]
    claim_penalty_intensified: int = 15
    claim_penalty_plain: int = 8
    claim_llm_verify: bool = False
    min_ratio: float = 0.85
    max_ratio: float = 1.30
    max_section_share: float = 0.35
    judge_enabled: bool = True


def load_eval_config() -> EvalConfig:
    """内置 eval.yaml 为底，用户文件浅合并覆盖同名字段。"""
    base = _read_yaml(BUILTIN_EVAL_PATH)
    user_path = user_resource_path("eval.yaml")
    user = _read_yaml(user_path) if user_path.is_file() else {}
    merged = {**base, **user}
    path = user_path if user else BUILTIN_EVAL_PATH
    try:
        policy = merged.get("policy") or {}
        raw_weights = merged.get("weights") or {}
        weights = {
            **_DEFAULT_WEIGHTS,
            **{
                name: float(value)
                for name, value in raw_weights.items()
                if name in _DEFAULT_WEIGHTS
            },
        }
        return EvalConfig(
            weights=weights,
            claim_penalty_intensified=int(policy["claim_penalty_intensified"]),
            claim_penalty_plain=int(policy["claim_penalty_plain"]),
            claim_llm_verify=bool(policy["claim_llm_verify"]),
            min_ratio=float(policy["min_ratio"]),
            max_ratio=float(policy["max_ratio"]),
            max_section_share=float(policy["max_section_share"]),
            judge_enabled=bool(policy["judge_enabled"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"eval.yaml 字段缺失或类型错误：{path}\n{exc}") from None


def load_baseline() -> dict[str, float]:
    """内置 baseline.yaml 为底，用户文件整体覆盖同名任务。

    未知任务 id 保留不删（deltas 只按本次实际任务取值，多余的键无副作用）。
    """
    base = _read_yaml(BUILTIN_BASELINE_PATH)
    user_path = user_resource_path("baseline.yaml")
    user = _read_yaml(user_path) if user_path.is_file() else {}
    merged = {**base, **user}
    path = user_path if user else BUILTIN_BASELINE_PATH
    try:
        return {
            name: float(value) for name, value in merged.get("tasks", {}).items()
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(f"baseline.yaml tasks 字段不合法：{path}\n{exc}") from None


def _read_yaml(path: Path) -> dict:
    """读取 YAML 文件为 dict；缺失返回空 dict，解析失败抛 ValueError。"""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"评估配置 YAML 解析失败：{path}\n{exc}") from None
    return data or {}
