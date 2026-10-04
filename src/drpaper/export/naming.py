"""导出产物命名：把论文题目转换为合法的文件/目录名。"""

from __future__ import annotations

import re

# Windows 文件名非法字符（\ / : * ? " < > |）及其替换符
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# Windows 保留设备名，不能直接作为文件/目录名
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """把论文题目转为合法的文件/目录名：替换非法字符、合并空白并去掉首尾空白与点。"""
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "未命名论文"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned
