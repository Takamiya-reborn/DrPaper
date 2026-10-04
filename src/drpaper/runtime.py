"""运行环境：用户资源目录定位（内置 + 用户两级资源的公共基建）。"""

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
