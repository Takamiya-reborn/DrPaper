"""固定标准任务集：五个解耦 mini 评测任务与编排入口 run_evaluation。

任务 ID 固定不变（跨跑可比的前提）；每个任务一个纯函数，
签名统一为 -> tuple[float | None, dict]，None 表示跳过该任务。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from drpaper.eval.claims import ClaimFinding, scan_claims
from drpaper.eval.config import EvalConfig
from drpaper.eval.judge import judge_language
from drpaper.eval.scorecard import Scorecard, TaskScore
from drpaper.llm.client import LLMClient
from drpaper.paper.draft import Draft
from drpaper.paper.markdown_parser import TableNode, parse
from drpaper.paper.metrics import expected_cell_errors
from drpaper.paper.wordcount import count_chars

# 确定性任务的每条扣分
_PENALTY_CITATION = 20
_PENALTY_NUMERIC = 25
_PENALTY_MISSING_SECTION = 30
_PENALTY_RATIO = 10
_PENALTY_SHARE = 10

# 硬门禁断言类型：强度/最高级断言未量化必须阻塞 finish_draft
HARD_KINDS = ("intensifier", "superlative")


def run_evaluation(
    draft: Draft,
    llm: LLMClient,
    issued_cells: list[str],
    config: EvalConfig,
    baseline: dict[str, float],
) -> Scorecard:
    """对当前草稿执行全部固定任务，返回评分卡。"""
    if not draft.markdown:
        draft.build_markdown()
    runners = [
        ("claim_audit", lambda: _claim_audit(draft, config, llm)),
        ("citation_consistency", lambda: _citation_consistency(draft)),
        ("structure_balance", lambda: _structure_balance(draft, config)),
        ("numeric_discipline", lambda: _numeric_discipline(draft, issued_cells)),
        ("language_judge", lambda: _language_judge(draft, llm, config)),
    ]
    tasks = [
        TaskScore(
            task=name,
            score=score,
            weight=config.weights.get(name, 0.0),
            detail=detail,
        )
        for name, runner in runners
        for score, detail in (runner(),)
    ]
    return Scorecard(tasks=tasks, baseline=baseline)


# ---- 各任务 ----


def _claim_audit(draft: Draft, config: EvalConfig, llm: LLMClient) -> tuple[float, dict]:
    findings = scan_claims([(s.name, s.content) for s in draft.sections])
    unquantified = [f for f in findings if f.backed_by == "none"]
    if config.claim_llm_verify and unquantified:
        unquantified = _llm_filter_claims(llm, unquantified)
    penalty = sum(
        config.claim_penalty_intensified
        if f.kind in HARD_KINDS
        else config.claim_penalty_plain
        for f in unquantified
    )
    return max(0.0, 100 - penalty), {
        "unquantified": [asdict(f) for f in unquantified],
        "n_findings": len(findings),
    }


def _citation_consistency(draft: Draft) -> tuple[float, dict]:
    """包装 Draft.citation_errors：每条错误扣分。"""
    errors = draft.citation_errors()
    return max(0.0, 100 - _PENALTY_CITATION * len(errors)), {"errors": errors}


def _structure_balance(draft: Draft, config: EvalConfig) -> tuple[float, dict]:
    """结构均衡：缺节、单节字数比越界、单节占全文比例超限。"""
    missing = draft.missing_sections()
    penalty = _PENALTY_MISSING_SECTION * len(missing)
    ratio_issues: list[str] = []
    share_issues: list[str] = []
    completed = [s for s in draft.sections if s.content]
    total = sum(count_chars(s.content) for s in completed)
    for section in completed:
        chars = count_chars(section.content)
        if section.budget > 0:
            ratio = chars / section.budget
            if not config.min_ratio <= ratio <= config.max_ratio:
                ratio_issues.append(f"{section.name}：{chars}/{section.budget} 字")
                penalty += _PENALTY_RATIO
        if total > 0 and chars / total > config.max_section_share:
            share_issues.append(f"{section.name}：占全文 {chars / total:.0%}")
            penalty += _PENALTY_SHARE
    return max(0.0, 100 - penalty), {
        "missing": missing,
        "ratio_issues": ratio_issues,
        "share_issues": share_issues,
    }


def _numeric_discipline(draft: Draft, issued_cells: list[str]) -> tuple[float, dict]:
    """包装 expected_cell_errors：表格†单元格必须原样来自 estimate_metric。"""
    cells = [
        cell
        for node in parse(draft.markdown)
        if isinstance(node, TableNode)
        for row in node.rows
        for cell in row
    ]
    errors = expected_cell_errors(cells, issued_cells)
    return max(0.0, 100 - _PENALTY_NUMERIC * len(errors)), {"errors": errors}


def _language_judge(
    draft: Draft, llm: LLMClient, config: EvalConfig
) -> tuple[float | None, dict]:
    """委托 LLM 锚定评分；禁用或失败时跳过（None 由计分时重分配权重）。"""
    if not config.judge_enabled:
        return None, {"skipped": "disabled"}
    result = judge_language(llm, draft.markdown)
    if result is None:
        return None, {"skipped": "llm_unavailable"}
    dims = [result[name] for name in ("density", "coherence", "tone")]
    score = sum((s - 1) / 4 * 100 for s in dims) / len(dims)
    return round(score, 1), result


# ---- claim_audit 的 LLM 复核 pass（eval.yaml claim_llm_verify 开启时生效）----

_VERIFY_SYSTEM = """你是学术写作审计员。逐条判断给定句子是否属于"缺乏量化支撑的优劣或程度断言"——
断言了更好/更差/更强等，但句内没有任何数字、区间或文献引用标记。
是则输出 true，仅陈述事实、趋势或有支撑的断言输出 false。
只输出与输入等长的 JSON 布尔数组，不要任何解释。"""


def _llm_filter_claims(
    llm: LLMClient, findings: list[ClaimFinding]
) -> list[ClaimFinding]:
    """让 LLM 复核正则命中的未量化主张，过滤误报；调用或解析失败时保留全部。"""
    messages = [
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": json.dumps([f.sentence for f in findings], ensure_ascii=False)},
    ]
    try:
        text = llm.chat(messages, [], {})
    except Exception:  # noqa: BLE001 - 复核失败退化为不过滤
        return findings
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        verdicts = json.loads(stripped)
    except json.JSONDecodeError:
        return findings
    if not isinstance(verdicts, list) or len(verdicts) != len(findings):
        return findings
    return [f for f, keep in zip(findings, verdicts) if keep is True]
