"""任务清单模型：多步骤任务的步骤与状态（pending / in_progress / completed）。

只管内存中的清单状态与 JSON 序列化，磁盘读写交给 agent.session；
模型经 update_todo / read_todo 工具读写清单，每完成一步立即更新。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 步骤状态全集：pending 未开始、in_progress 进行中、completed 已完成
STATUSES = ("pending", "in_progress", "completed")


@dataclass
class TodoStep:
    """清单中的一步：内容描述与当前状态。"""

    content: str
    status: str = "pending"


@dataclass
class TodoList:
    """一个任务的步骤清单：整体替换写入，随会话状态持久化。"""

    steps: list[TodoStep] = field(default_factory=list)

    def replace(self, raw: Any) -> None:
        """用 LLM 提交的步骤列表整体替换清单；非法输入抛 ValueError。

        整体替换而非逐条打补丁：清单原子性强，避免增量更新造成状态漂移。
        """
        if not isinstance(raw, list) or not raw:
            raise ValueError("steps 不能为空，须为 [{content, status}, ...] 列表")
        steps: list[TodoStep] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("每个步骤必须是 {content, status} 对象")
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "")).strip()
            if not content:
                raise ValueError("存在空步骤内容")
            if status not in STATUSES:
                raise ValueError(
                    f"非法步骤状态: {status}（可用: {' / '.join(STATUSES)}）"
                )
            steps.append(TodoStep(content=content, status=status))
        self.steps = steps

    def load_json(self, data: Any) -> None:
        """从磁盘 JSON 恢复清单；损坏条目跳过，不清空其他已恢复状态。"""
        if not isinstance(data, list):
            return
        steps = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "")).strip()
            if content and status in STATUSES:
                steps.append(TodoStep(content=content, status=status))
        self.steps = steps

    def to_json(self) -> list[dict[str, str]]:
        return [{"content": s.content, "status": s.status} for s in self.steps]

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    @property
    def total(self) -> int:
        return len(self.steps)

    def render(self) -> str:
        """渲染为复选框视图（系统提示词与启动提示用）。"""
        lines = []
        for step in self.steps:
            if step.status == "completed":
                lines.append(f"- [x] {step.content}")
            elif step.status == "in_progress":
                lines.append(f"- [ ] {step.content}（进行中）")
            else:
                lines.append(f"- [ ] {step.content}")
        return "\n".join(lines)

    def progress(self) -> str:
        return f"{self.done}/{self.total} 步已完成"
