"""Agent 核心：会话状态与 tool-calling 主循环。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from drpaper.agent.prompts import build_system_prompt
from drpaper.agent.tools import TOOL_SCHEMAS, ToolBox
from drpaper.literature.arxiv import ArxivProvider
from drpaper.llm.client import ChatCallbacks, LLMClient
from drpaper.paper.draft import Draft
from drpaper.skills.manager import SkillManager

# 用户输入中的目标字数，如 "8000 字"
_TARGET_PATTERN = re.compile(r"(\d{3,5})\s*字")


@dataclass
class PaperAgent:
    """DrPaper Agent：多轮对话 + 检索 + 分节起草/诊断 + 导出。"""

    llm: LLMClient
    output_dir: str
    target_chars: int = 5000
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        skills = SkillManager()
        self._skill_index = skills.index_prompt()
        # messages 传引用给 ToolBox（折叠旧稿用），后续只 append 不重新绑定
        self._toolbox = ToolBox(
            provider=ArxivProvider(),
            draft=Draft(),
            output_dir=self.output_dir,
            skills=skills,
            messages=self.messages,
            target_chars=self.target_chars,
        )
        self.messages.append(
            {"role": "system", "content": build_system_prompt(self._skill_index, self.target_chars)}
        )
        self.last_target_detected = False

    @property
    def draft(self) -> Draft:
        """当前会话的草稿与文献库。"""
        return self._toolbox.draft

    def chat(self, user_input: str, callbacks: ChatCallbacks | None = None) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self._sync_target(user_input)
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

    def _sync_target(self, user_input: str) -> None:
        """解析用户输入中的目标字数，同步给工具校验与系统提示词。"""
        match = _TARGET_PATTERN.search(user_input)
        self.last_target_detected = match is not None
        if not match:
            return
        target = int(match.group(1))
        if target == self.target_chars:
            return
        self.target_chars = target
        self._toolbox.target_chars = target
        self.messages[0]["content"] = build_system_prompt(self._skill_index, target)
