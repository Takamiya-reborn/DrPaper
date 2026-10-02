"""Skill 管理：扫描内置与用户 skills，构建索引，按需加载正文与参考文件。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

USER_SKILLS_DIR = Path.home() / ".drpaper" / "skills"


@dataclass
class Skill:
    """一个 skill：名称、触发时机与 SKILL.md 路径。"""

    name: str
    description: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 的 frontmatter，返回 (字段字典, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    _, frontmatter, body = text.split("---", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and value.strip():
            fields[key.strip()] = value.strip()
    return fields, body.strip()


class SkillManager:
    """内置 + 用户 skill 集合：索引常驻提示词，正文按需加载。"""

    def __init__(
        self,
        base_dir: Path | None = None,
        user_dir: Path | None = None,
    ) -> None:
        base = base_dir or Path(__file__).parent / "builtin"
        user = user_dir or USER_SKILLS_DIR
        by_name: dict[str, Skill] = {}
        # 用户目录后扫描，同名 skill 覆盖内置版
        for directory, strict in ((base, False), (user, True)):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*/SKILL.md")):
                fields, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
                if "name" in fields and "description" in fields:
                    by_name[fields["name"]] = Skill(fields["name"], fields["description"], path)
                elif strict:
                    print(f"[警告] 跳过无效 skill（缺少 name/description）: {path}", file=sys.stderr)
        self.skills = list(by_name.values())

    def index_prompt(self) -> str:
        """生成注入系统提示词的紧凑索引：每条仅名称与触发时机。"""
        return "\n".join(f"- {s.name}: {s.description}" for s in self.skills)

    def load(self, name: str, file: str | None = None) -> str:
        """加载 skill 正文；file 指定时加载其 references 下的参考文件。"""
        skill = next((s for s in self.skills if s.name == name), None)
        if skill is None:
            raise KeyError(f"未知 skill: {name}（可用: {', '.join(s.name for s in self.skills)}）")
        target = skill.path if file is None else self._reference_path(skill, file)
        _, body = _parse_frontmatter(target.read_text(encoding="utf-8"))
        return body

    def _reference_path(self, skill: Skill, file: str) -> Path:
        """解析 skill 目录内的参考文件路径，禁止越出目录。"""
        root = skill.path.parent
        target = (root / file).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError(f"非法路径: {file}")
        if not target.is_file():
            raise FileNotFoundError(f"参考文件不存在: {file}")
        return target
