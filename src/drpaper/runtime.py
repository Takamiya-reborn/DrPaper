"""运行环境：目录定位（用户资源 + 运行工作区的公共基建）。"""

from __future__ import annotations

import sys
from pathlib import Path


def user_resource_dir() -> Path:
    """用户资源根目录：打包运行取 exe 同级目录，源码运行取 ~/.drpaper/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.home() / ".drpaper"


def user_resource_path(name: str) -> Path:
    """用户资源路径（文件或目录）：user_resource_dir() / name。"""
    return user_resource_dir() / name


def workspace_dir() -> Path:
    """运行工作区目录（.drpaper/）：运行期暂存文件统一放这里。

    学习 Claude Code 在项目内建 .claude/ 的做法：跟着程序走，不进用户目录——
    打包运行取 exe 同级目录，源码运行取当前工作目录（与 .output 的定位规则一致）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / ".drpaper"
    return Path.cwd() / ".drpaper"


def ensure_workspace_dir() -> Path:
    """确保工作区目录存在并返回；暂存文件写入前调用。"""
    path = workspace_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
