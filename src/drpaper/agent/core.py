"""Agent 核心：会话状态与 tool-calling 主循环。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from drpaper.agent.prompts import build_system_prompt
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.agent.tools import ToolBox
from drpaper.export.style_profile import StyleProfile
from drpaper.literature.aggregator import default_aggregator
from drpaper.llm.client import ChatCallbacks, LLMClient
from drpaper.paper.draft import Draft
from drpaper.skills.manager import SkillManager
from drpaper.workflows import WorkflowManager


@dataclass
class PaperAgent:
    """DrPaper Agent：多轮对话 + 检索 + 分节起草/诊断 + 导出。"""

    llm: LLMClient
    output_dir: str
    style: StyleProfile
    target_chars: int = 5000
    library_paths: Sequence[str] = ()  # 用户外接文献库路径（CLI --library）
    user_workflows_dir: Path | None = None  # 用户 workflow 文档目录（None 取默认位置）
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        skills = SkillManager()
        self._skill_index = skills.index_prompt()
        workflows = WorkflowManager(user_dir=self.user_workflows_dir)
        self._workflow_index = workflows.index_prompt()
        provider = default_aggregator(library_paths=self.library_paths)
        self.library_warnings = provider.warnings  # 文献文件加载警告，启动时向用户展示
        # messages 传引用给 ToolBox（折叠旧稿与重建系统提示词用），后续只 append 不重新绑定
        self._toolbox = ToolBox(
            provider=provider,
            llm=self.llm,
            draft=Draft(),
            output_dir=self.output_dir,
            style=self.style,
            skills=skills,
            skill_index=self._skill_index,
            workflows=workflows,
            workflow_index=self._workflow_index,
            messages=self.messages,
            target_chars=self.target_chars,
        )
        self.messages.append(
            {
                "role": "system",
                "content": build_system_prompt(
                    self._skill_index, self._workflow_index, self.target_chars
                ),
            }
        )

    @property
    def draft(self) -> Draft:
        """当前会话的草稿与文献库。"""
        return self._toolbox.draft

    def chat(self, user_input: str, callbacks: ChatCallbacks | None = None) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self._toolbox.callbacks = callbacks  # 子代理透传工具事件、单独统计 token
        self.messages.append({"role": "user", "content": user_input})
        reply = self.llm.chat(
            self.messages,
            TOOL_SCHEMAS,
            self._toolbox.handlers(),
            callbacks=callbacks,
        )
        if reply:  # 空回复不进历史
            self.messages.append({"role": "assistant", "content": reply})
        return reply
