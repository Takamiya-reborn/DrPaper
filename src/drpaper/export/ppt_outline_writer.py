"""Generate a structured presentation handoff from a completed paper."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from drpaper.paper.draft import Draft

_SYSTEM_PROMPT = """你是学术汇报编辑。请把论文内容整理成可以交给 PPT 制作 Agent 的结构化大纲。
只能使用论文中出现的信息，不得补造数字、结论或引用。每页包含 3 到 5 个要点。
只输出 JSON，不要输出 Markdown 代码围栏或解释文字。JSON 结构必须是：
{
  \"title\": string,
  \"audience\": string,
  \"estimated_minutes\": integer,
  \"slides\": [{
    \"number\": integer,
    \"title\": string,
    \"summary\": string,
    \"key_points\": [string],
    \"evidence\": [string],
    \"visual_suggestion\": string,
    \"speaker_notes\": string
  }],
  \"references\": [{\"number\": integer, \"text\": string}]
}
"""


def _fallback_outline(draft: Draft) -> dict[str, Any]:
    """Create a usable outline when the model is unavailable or returns invalid JSON."""
    slides: list[dict[str, Any]] = []
    for number, section in enumerate(draft.sections, start=1):
        content = " ".join(section.content.split())
        points = [
            part.strip() for part in re.split(r"[。！？；]", content) if part.strip()
        ]
        slides.append(
            {
                "number": number,
                "title": section.name,
                "summary": points[0][:160] if points else "本节暂无内容。",
                "key_points": points[:5] or ["本节暂无内容。"],
                "evidence": sorted(set(re.findall(r"\[\d+(?:,\d+)*\]", content))),
                "visual_suggestion": "根据本节内容选择结构图、流程图或数据图表。",
                "speaker_notes": content[:500],
            }
        )
    return {
        "title": draft.title,
        "audience": "学术汇报",
        "estimated_minutes": max(5, len(slides) * 2),
        "slides": slides,
        "references": [
            {"number": i, "text": text}
            for i, text in enumerate(draft.reference_lines(), start=1)
        ],
    }


def _extract_json(text: str) -> dict[str, Any]:
    """Accept plain JSON and the occasional fenced JSON response."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE
        )
    value = json.loads(candidate)
    if not isinstance(value, dict) or not isinstance(value.get("slides"), list):
        raise ValueError("PPT 大纲 JSON 缺少 slides 数组")  # noqa: TRY004
    slides = []
    for number, raw_slide in enumerate(value["slides"], start=1):
        if not isinstance(raw_slide, dict):
            raise ValueError("PPT 大纲中的页面必须是对象")  # noqa: TRY004
        points = raw_slide.get("key_points", [])
        if not isinstance(points, list):
            points = [str(points)]
        slides.append(
            {
                "number": raw_slide.get("number", number),
                "title": str(raw_slide.get("title", f"第 {number} 页")),
                "summary": str(raw_slide.get("summary", "")),
                "key_points": [str(point) for point in points[:5]] or ["暂无要点。"],
                "evidence": [str(item) for item in raw_slide.get("evidence", [])],
                "visual_suggestion": str(raw_slide.get("visual_suggestion", "")),
                "speaker_notes": str(raw_slide.get("speaker_notes", "")),
            }
        )
    value["slides"] = slides
    value.setdefault("title", "论文汇报")
    value.setdefault("audience", "学术汇报")
    value.setdefault("estimated_minutes", max(5, len(slides) * 2))
    value.setdefault("references", [])
    return value


def _model_outline(draft: Draft, llm: Any) -> dict[str, Any]:
    prompt = (
        f"论文标题：{draft.title}\n\n"
        f"论文正文：\n{draft.markdown}\n\n"
        "参考文献：\n" + "\n".join(draft.reference_lines())
    )
    response = llm.chat(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        tools=[],
        handlers={},
        max_rounds=1,
    )
    return _extract_json(response)


def _markdown(outline: dict[str, Any]) -> str:
    lines = [f"# {outline.get('title', '论文汇报')} PPT 大纲", ""]
    lines.append(f"- 汇报对象：{outline.get('audience', '学术汇报')}")
    lines.append(f"- 建议时长：{outline.get('estimated_minutes', 15)} 分钟")
    lines.append("")
    for slide in outline["slides"]:
        lines.extend(
            [
                f"## {slide['number']}. {slide['title']}",
                "",
                f"**本页概括**：{slide['summary']}",
                "",
                "**核心要点**：",
                *[f"- {point}" for point in slide["key_points"]],
                f"\n**证据引用**：{'、'.join(slide.get('evidence', [])) or '无'}",
                f"**视觉建议**：{slide.get('visual_suggestion', '')}",
                f"**讲稿提示**：{slide.get('speaker_notes', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def export_ppt_outline(draft: Draft, output_dir: Path, llm: Any) -> tuple[Path, Path]:
    """Write JSON and Markdown PPT handoff files and return both paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        outline = _model_outline(draft, llm)
    except Exception:  # noqa: BLE001 - 模型失败时必须回退到确定性大纲
        outline = _fallback_outline(draft)
    json_path = output_dir / "ppt_outline.json"
    markdown_path = output_dir / "ppt_outline.md"
    json_path.write_text(
        json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(_markdown(outline), encoding="utf-8")
    return json_path, markdown_path
