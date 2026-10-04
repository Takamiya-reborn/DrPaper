"""样式档案：内置 + 用户两级目录管理排版规范 YAML，供 docx 导出消费。

与 skills 同款的两级目录模式：
- 内置目录随包分发（打包时由 package.spec 带入）
- 用户目录：打包运行取 exe 同级的 profiles/，源码运行取 ~/.drpaper/profiles
- 同名档案用户目录覆盖内置；用户目录中的 default.yaml 优先作为默认档案
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

BUILTIN_PROFILES_DIR = Path(__file__).parent / "profiles"
BUILTIN_PROFILE_STEM = "thesis-generic"


@dataclass(frozen=True)
class StyleProfile:
    """docx 导出所需的排版参数（单位：pt / cm / 字符）。"""

    name: str
    cn_body_font: str
    cn_heading_font: str
    en_font: str
    title_pt: float
    heading1_pt: float
    body_pt: float
    reference_pt: float
    margin_cm: float
    line_spacing: float
    first_line_indent_chars: int


def user_profiles_dir() -> Path:
    """用户样式档案目录：打包运行取 exe 同级 profiles/，源码运行取 ~/.drpaper/profiles。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "profiles"
    return Path.home() / ".drpaper" / "profiles"


def list_profiles() -> dict[str, Path]:
    """扫描两级目录返回可用档案（文件名主干 → 路径），用户目录覆盖同名内置。"""
    profiles: dict[str, Path] = {}
    for directory in (BUILTIN_PROFILES_DIR, user_profiles_dir()):
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                profiles[path.stem] = path
    return profiles


def load_profile(name: str | Path | None = None) -> StyleProfile:
    """加载样式档案：显式路径 → 按名称查找 → 默认档案（用户 default.yaml 或内置通用规范）。"""
    if isinstance(name, Path):
        return _parse(name)

    profiles = list_profiles()
    key = name or ("default" if "default" in profiles else BUILTIN_PROFILE_STEM)
    resolved = profiles.get(key)
    if resolved is None:
        available = "、".join(profiles) or "（目录为空）"
        raise SystemExit(f"找不到样式档案「{key}」，可用：{available}")
    return _parse(resolved)


def _parse(path: Path) -> StyleProfile:
    """读取并校验单个样式档案，文件缺失或字段不合法时抛出带指引的 SystemExit。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"找不到样式档案：{path}") from None
    except yaml.YAMLError as exc:
        raise SystemExit(f"样式档案 YAML 解析失败：{path}\n{exc}") from None

    try:
        return StyleProfile(
            name=str(data.get("name", path.stem)),
            cn_body_font=data["fonts"]["cn_body"],
            cn_heading_font=data["fonts"]["cn_heading"],
            en_font=data["fonts"]["en"],
            title_pt=float(data["sizes"]["title"]["pt"]),
            heading1_pt=float(data["sizes"]["heading1"]["pt"]),
            body_pt=float(data["sizes"]["body"]["pt"]),
            reference_pt=float(data["sizes"]["reference"]["pt"]),
            margin_cm=float(data["page"]["margin_cm"]),
            line_spacing=float(data["paragraph"]["line_spacing"]),
            first_line_indent_chars=int(data["paragraph"]["first_line_indent_chars"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"样式档案缺少或类型错误的字段：{path}\n{exc}") from None
