"""Skill 管理：扫描内置与用户 skills，构建索引，按需加载正文与参考文件。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from drpaper.runtime import user_resource_path

# 打包运行取 exe 同级 skills/，源码运行取 ~/.drpaper/skills（与 profiles、sources.yaml 一致）
USER_SKILLS_DIR = user_resource_path("skills")


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
        # 本会话已加载过完整正文的 skill 名，避免重复加载把全文再灌一遍历史
        self._loaded: set[str] = set()

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

    def load_once(self, name: str, file: str | None = None) -> str:
        """加载 skill 内容；整篇正文（file=None）会话内去重，参考文件不去重。"""
        content = self.load(name, file)
        if file is not None:
            return content
        if name in self._loaded:
            return f"skill「{name}」的完整内容已在本会话上下文中，无需重复加载。"
        self._loaded.add(name)
        return content

    def _reference_path(self, skill: Skill, file: str) -> Path:
        """解析 skill 目录内的参考文件路径，禁止越出目录。

        约定 references 文件按裸文件名传入（如 'sections.md'），
        优先在 references/ 下解析，兼容直接传相对路径的用法。
        """
        root = skill.path.parent
        candidates = [root / "references" / file, root / file]
        for target in (c.resolve() for c in candidates):
            if not target.is_relative_to(root.resolve()):
                raise ValueError(f"非法路径: {file}")
            if target.is_file():
                return target
        raise FileNotFoundError(f"参考文件不存在: {file}")
