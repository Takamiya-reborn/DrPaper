"""Workflow 管理：扫描内置与用户 workflows，按阶段渐进注入流程文档。

与 skills 同一套约定：一个 workflow 一个文件夹，WORKFLOW.md 声明名称与
适用时机，stages/ 下每个阶段一个 Markdown（frontmatter 声明阶段名与
入口工具，文件名带序号决定阶段顺序）。阶段划分、验收标准、注意事项
全部住在 Markdown 里——调整流程只改文档，不改 Python 代码。

运行时模型调用某阶段的入口工具即视为进入该阶段：首次进入（阶段只
前进不回退）时把该阶段文档随工具结果注入一次，之后不重复灌历史。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from drpaper.runtime import user_resource_path

# 打包运行取 exe 同级 workflows/，源码运行取 ~/.drpaper/workflows（与 skills 一致）
USER_WORKFLOWS_DIR = user_resource_path("workflows")


@dataclass
class Stage:
    """一个阶段：阶段名、入口工具与流程文档正文。"""

    name: str
    entry_tools: tuple[str, ...]
    body: str


@dataclass
class Workflow:
    """一个 workflow：名称、适用时机、总则与阶段列表（按文件名序号排序）。"""

    name: str
    description: str
    overview: str
    stages: list[Stage]


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 Markdown 的 frontmatter，返回 (字段字典, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    _, frontmatter, body = text.split("---", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and value.strip():
            fields[key.strip()] = value.strip()
    return fields, body.strip()


class WorkflowManager:
    """内置 + 用户 workflow 集合：索引常驻提示词，阶段文档进入时注入。"""

    def __init__(
        self,
        base_dir: Path | None = None,
        user_dir: Path | None = None,
    ) -> None:
        base = base_dir or Path(__file__).parent / "builtin"
        user = user_dir or USER_WORKFLOWS_DIR
        by_name: dict[str, Workflow] = {}
        # 用户目录后扫描，同名 workflow 覆盖内置版（改流程即改用户侧文档）
        for directory, strict in ((base, False), (user, True)):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*/WORKFLOW.md")):
                fields, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
                if "name" not in fields or "description" not in fields:
                    if strict:
                        print(
                            f"[警告] 跳过无效 workflow（缺少 name/description）: {path}",
                            file=sys.stderr,
                        )
                    continue
                workflow = Workflow(
                    fields["name"], fields["description"], body, self._load_stages(path.parent / "stages")
                )
                if not workflow.stages and strict:
                    print(f"[警告] workflow 无有效阶段: {path}", file=sys.stderr)
                by_name[workflow.name] = workflow
        self.workflows = list(by_name.values())
        # workflow 名 → 已注入的最大阶段序号；只前进不回退，重入早期阶段不重复注入
        self._entered: dict[str, int] = {}

    def _load_stages(self, directory: Path) -> list[Stage]:
        stages: list[Stage] = []
        if not directory.is_dir():
            return stages
        for path in sorted(directory.glob("*.md")):
            fields, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if "stage" not in fields:
                print(f"[警告] 跳过无 stage 字段的阶段文档: {path}", file=sys.stderr)
                continue
            tools = tuple(
                t.strip() for t in fields.get("entry_tools", "").split(",") if t.strip()
            )
            stages.append(Stage(fields["stage"], tools, body))
        return stages

    def index_prompt(self) -> str:
        """生成注入系统提示词的紧凑索引：每条仅名称、适用时机与阶段链。"""
        lines = []
        for wf in self.workflows:
            chain = " → ".join(s.name for s in wf.stages)
            lines.append(f"- {wf.name}: {wf.description}（阶段: {chain}）")
        return "\n".join(lines)

    def enter_doc(self, tool: str) -> str:
        """模型调用了某阶段的入口工具：首次进入更晚阶段时返回注入文档，否则空串。

        首次启用某 workflow 时附带其总则，之后只注入阶段文档本身。
        """
        hit: tuple[Workflow, int, Stage] | None = None
        for wf in self.workflows:
            for pos, stage in enumerate(wf.stages):
                if tool in stage.entry_tools:
                    hit = (wf, pos, stage)
                    break
            if hit:
                break
        if hit is None:
            return ""
        wf, pos, stage = hit
        if pos <= self._entered.get(wf.name, -1):
            return ""
        first = wf.name not in self._entered
        self._entered[wf.name] = pos
        parts = []
        if first:
            parts.append(f"【workflow：{wf.name}】{wf.overview}")
        parts.append(f"【当前阶段：{stage.name}】按以下流程文档执行：\n\n{stage.body}")
        return "\n\n".join(parts)
