"""Agent 核心：会话状态与 tool-calling 主循环。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from drpaper.agent.prompts import build_system_prompt
from drpaper.agent.tools import TOOL_SCHEMAS, ToolBox
from drpaper.literature.arxiv import ArxivProvider
from drpaper.llm.client import LLMClient
from drpaper.paper.draft import Draft
from drpaper.skills.manager import SkillManager


@dataclass
class PaperAgent:
    """DrPaper Agent：多轮对话 + 检索 + 起草/诊断 + 导出。"""

    llm: LLMClient
    output_dir: str
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        skills = SkillManager()
        # messages 传引用给 ToolBox（折叠旧稿用），后续只 append 不重新绑定
        self._toolbox = ToolBox(
            provider=ArxivProvider(),
            draft=Draft(),
            output_dir=self.output_dir,
            skills=skills,
            messages=self.messages,
        )
        self.messages.append(
            {"role": "system", "content": build_system_prompt(skills.index_prompt())}
        )

    @property
    def draft(self) -> Draft:
        """当前会话的草稿与文献库。"""
        return self._toolbox.draft

    def chat(self, user_input: str, on_text: Callable[[str], None] | None = None) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self.messages.append({"role": "user", "content": user_input})
        reply = self.llm.chat(
            self.messages,
            TOOL_SCHEMAS,
            self._toolbox.handlers(),
            on_text=on_text,
        )
        self.messages.append({"role": "assistant", "content": reply})
        return reply
