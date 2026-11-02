"""子代理层：按类别分层的独立 agent loop（调研 / 写作 / 审校）。

每个子代理拥有独立的消息历史与工具集，跑完整的 tool-calling loop，
只向主循环（drpaper.agent）返回结构化摘要——调研返回资料卡、
写作返回章节摘要、审校返回意见列表，中间过程不进主对话。
"""

from drpaper.agents.research.agent import ResearchAgent
from drpaper.agents.reviewer.agent import ReviewerAgent
from drpaper.agents.writer.agent import WriterAgent

__all__ = ["ResearchAgent", "ReviewerAgent", "WriterAgent"]
