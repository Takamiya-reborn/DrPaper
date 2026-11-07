"""会话状态持久化：任务清单与草稿进度落盘（session.json），支撑断点续跑。

快照内容由 ToolBox.snapshot() 组装（它最清楚自己的状态结构），本模块只管
磁盘读写：每次工具调用后整文件覆写，进程被杀最多丢一次工具调用的增量；
重启时 PaperAgent 读取并恢复，从清单第一个未完成步骤继续。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# 会话状态文件名（位于 output_dir 根目录）
SESSION_FILENAME = "session.json"

# 快照格式版本：不匹配时视为旧格式/损坏，忽略而非带病恢复
SUPPORTED_VERSION = 1


class SessionStore:
    """output_dir 下的会话状态文件读写。"""

    def __init__(self, output_dir: str) -> None:
        self.path = Path(output_dir) / SESSION_FILENAME

    def save(self, state: dict[str, Any]) -> Path:
        """整文件覆写当前快照，返回文件路径。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SUPPORTED_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            **state,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.path

    def load(self) -> dict[str, Any] | None:
        """读取上次会话快照；不存在、损坏或版本不识别时返回 None（重新开始）。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict) or data.get("version") != SUPPORTED_VERSION:
            return None
        return data
