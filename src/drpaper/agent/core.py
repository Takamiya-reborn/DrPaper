"""Agent 核心：会话状态与 tool-calling 主循环。"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from drpaper.agent.compaction import Compactor, context_window
from drpaper.agent.prompts import build_resume_block, build_system_prompt
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.agent.session import SessionStore
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
            store=SessionStore(self.output_dir),
            messages=self.messages,
            target_chars=self.target_chars,
        )
        # 上下文压缩器：对消息历史无记忆，主循环与子代理共用同一个实例
        self._compactor = Compactor(
            llm=self.llm,
            window_tokens=context_window(),
            state_snapshot=self._toolbox.state_block,
        )
        self._toolbox.compactor = self._compactor
        resume_block = self._restore_session()
        self._toolbox.resume_block = resume_block
        self.resumed = bool(resume_block)  # 启动时是否从上次会话恢复（CLI 提示用）
        self.messages.append(
            {
                "role": "system",
                "content": build_system_prompt(
                    self._skill_index, self._workflow_index, self.target_chars
                )
                + resume_block,
            }
        )

    def _restore_session(self) -> str:
        """读取磁盘会话快照恢复状态，返回断点恢复提示块（无可续任务时为空串）。"""
        store: SessionStore = self._toolbox.store
        state = store.load()
        if not state:
            return ""
        try:
            self._toolbox.restore(state)
        except Exception as exc:  # noqa: BLE001 - 快照损坏时不带病恢复，重新开始
            print(f"[警告] 会话状态恢复失败，已重新开始: {exc}", file=sys.stderr)
            return ""
        todo = self._toolbox.todo
        if not todo.steps or todo.done == todo.total:
            return ""
        draft = self._toolbox.draft
        if draft.title:
            done = len(draft.sections) - len(draft.missing_sections())
            confirmed = "提纲已确认" if self._toolbox._outline_confirmed else "提纲待用户确认"
            draft_summary = (
                f"已恢复草稿「{draft.title}」：大纲共 {len(draft.sections)} 节、"
                f"已完成 {done} 节（{confirmed}），文献库 {len(draft.references)} 条。"
            )
        else:
            draft_summary = "上次会话尚未提交提纲。"
        return build_resume_block(todo.render(), todo.progress(), draft_summary)

    @property
    def resume_notice(self) -> str | None:
        """CLI 启动提示：检测到可续任务时给出接续指引。"""
        if not self.resumed:
            return None
        todo = self._toolbox.todo
        return (
            f"检测到上次未完成的任务（{todo.progress()}），"
            "草稿与文献库已恢复：直接发送「继续」即可接续，无需重做已完成步骤。"
        )

    @property
    def draft(self) -> Draft:
        """当前会话的草稿与文献库。"""
        return self._toolbox.draft

    def chat(self, user_input: str, callbacks: ChatCallbacks | None = None) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self._toolbox.callbacks = callbacks  # 子代理透传工具事件、单独统计 token
        self._toolbox.note_user_turn()  # 真实用户输入解锁提纲确认（与压缩后的历史无关）
        self._compactor.usage_cb = callbacks.on_usage if callbacks else None
        self.messages.append({"role": "user", "content": user_input})
        reply = self.llm.chat(
            self.messages,
            TOOL_SCHEMAS,
            self._toolbox.handlers(),
            callbacks=callbacks,
            compactor=self._compactor,
        )
        if reply:  # 空回复不进历史
            self.messages.append({"role": "assistant", "content": reply})
        return reply
