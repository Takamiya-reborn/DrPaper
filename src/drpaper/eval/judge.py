"""LLM 锚定评分：language_judge 任务的判分调用。

仿 paper.metrics 的非工具调用模式：llm.chat(messages, [], {}) + JSON 容错，
解析或校验失败返回 None（宁可跳过，不给假分数）。
"""

from __future__ import annotations

import json
import re

from drpaper.llm.client import LLMClient

_DIMENSIONS = ("density", "coherence", "tone")

_JUDGE_SYSTEM = """你是中文学术论文语言质量评审。对给定正文按三个维度打 1~5 整数分，只输出 JSON：
{"density": 整数, "coherence": 整数, "tone": 整数, "evidence": {"density": "原文短语", "coherence": "原文短语", "tone": "原文短语"}}

评分锚点（只锚 1/3/5，中间分介于两者之间）：
density 实质密度：5=几乎无套话，每句承载具体机制、数据或论据；3=有少量"综上所述/具有重要意义"式填充但主体扎实；1=大量空洞套话与重复铺垫。
coherence 逻辑连贯：5=段落承接明确，"不足→方法→结果→结论"闭环；3=个别段落间跳跃但整体可跟随；1=论断间缺乏因果关联，读来割裂。
tone 学术语气：5=客观严谨，无营销化措辞；3=偶有口语化或夸大表述；1=明显口语化或推销式（如"完美解决""效果拔群"）。

纪律：只依据正文本身评分；evidence 必须逐字引用正文短语（≤20 字）；证据不足时取较低分。"""


def judge_language(llm: LLMClient, markdown: str) -> dict | None:
    """三维度锚定评分；调用、解析或校验任一失败返回 None。"""
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": markdown},
    ]
    try:
        text = llm.chat(messages, [], {})
    except Exception:  # noqa: BLE001 - 判分失败不应中断评估，退化为跳过该任务
        return None
    data = _parse_json_object(text)
    if data is None:
        return None
    scores = {}
    for name in _DIMENSIONS:
        value = data.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            return None
        scores[name] = value
    evidence = data.get("evidence")
    scores["evidence"] = {
        name: str(evidence.get(name, ""))[:20]
        if isinstance(evidence, dict)
        else ""
        for name in _DIMENSIONS
    }
    return scores


def _parse_json_object(text: str) -> dict | None:
    """解析 LLM 输出中的 JSON 对象，容忍代码块包裹；解析失败返回 None。"""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
