"""P0 改造验证：function calling 目标字数工具 + agent loop 失败回传自纠。

用假流式客户端驱动真实的 LLMClient.chat 循环与 ToolBox 处理函数，
不访问网络；验证点对应 plan.md 的 P0-1 / P0-2 验收项。
"""

from __future__ import annotations

import pytest

from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.paper.metrics import extract_records
from fake_llm import agent_with, chunk as _chunk, tool_call as _tool_call


# ---- P0-1：目标字数走 function calling ----


def test_set_target_chars_via_tool(tmp_path) -> None:
    rounds = [
        [_chunk(tool_calls=[_tool_call("set_target_chars", {"target_chars": 8000})], finish="tool_calls")],
        [_chunk(content="好的，目标字数已调整为 8000 字。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    reply = agent.chat("改成 8000 字")
    assert agent._toolbox.target_chars == 8000
    assert "8000 字" in agent.messages[0]["content"]  # 系统提示词同步重建
    assert reply.startswith("好的")


def test_every_schema_tool_has_handler(tmp_path) -> None:
    agent = agent_with([], tmp_path)
    schema_names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    assert schema_names == set(agent._toolbox.handlers())


def test_target_below_floor_rejected(tmp_path) -> None:
    rounds = [
        [_chunk(tool_calls=[_tool_call("set_target_chars", {"target_chars": 100})], finish="tool_calls")],
        [_chunk(content="目标字数过低。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("就要 100 字")
    assert agent._toolbox.target_chars == 5000  # 未被修改


# ---- P0-2：失败原样回传，模型自行纠正 ----


def test_tool_error_fed_back_and_self_corrects(tmp_path) -> None:
    rounds = [
        [_chunk(tool_calls=[_tool_call("begin_draft", {
            "title": "测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls")],
        # 提纲待确认：停止写作，向用户请求确认
        [_chunk(content="以上是提纲，请确认。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")
    assert not agent.draft.sections[0].content  # 未确认前不动笔

    agent.llm._client.chat.completions.rounds = [
        [_chunk(tool_calls=[_tool_call("confirm_outline", {})], finish="tool_calls")],
        # 人为制造失败：节名不在大纲中
        [_chunk(tool_calls=[_tool_call("submit_section", {
            "name": "2 不存在的节", "content": "正文",
        })], finish="tool_calls")],
        # 模型看到错误后自行纠正
        [_chunk(tool_calls=[_tool_call("submit_section", {
            "name": "1 引言", "content": "研" * 100,
        })], finish="tool_calls")],
        [_chunk(content="初稿完成。", finish="stop")],
    ]
    reply = agent.chat("可以，开始写")
    assert reply == "初稿完成。"
    tool_results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert any("节名不在大纲中" in text for text in tool_results)  # 错误原样在历史中
    assert agent.draft.sections[0].content == "研" * 100


def test_max_rounds_limit_reports_state(tmp_path) -> None:
    agent = agent_with(
        [[_chunk(tool_calls=[_tool_call("search_literature", {"query": "llm"})], finish="tool_calls")]],
        tmp_path,
    )
    reply = agent.llm.chat(
        [{"role": "user", "content": "一直检索"}],
        TOOL_SCHEMAS,
        agent._toolbox.handlers(),
        max_rounds=2,
    )
    assert "上限 2 轮" in reply


def test_unknown_file_error_fed_back(tmp_path) -> None:
    """P0-2 验收项：文件路径错误这类工具异常作为 tool_result 回传而非中断。"""
    rounds = [
        [_chunk(tool_calls=[_tool_call("load_skill", {"name": "paper-drafting", "file": "no-such.md"})], finish="tool_calls")],
        [_chunk(content="该参考文件不存在。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("加载 paper-drafting 的 no-such.md")
    tool_results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert any("error" in text for text in tool_results)


# ---- P0-2：删除静默回退分支 ----


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def chat(self, _messages, _tools, _handlers, callbacks=None, max_rounds=40):
        return self.reply


def test_ppt_outline_no_silent_fallback(tmp_path) -> None:
    """模型输出非法 JSON 时应抛错（交由 agent 循环重试），不再回退确定性大纲。"""
    from drpaper.export.ppt_outline_writer import export_ppt_outline

    with pytest.raises(Exception):
        export_ppt_outline(_draft_stub(), tmp_path, _StubLLM("这不是 JSON"))
    assert not (tmp_path / "ppt_outline.json").exists()


def test_metric_extraction_failure_propagates() -> None:
    """抽取调用失败应抛出而非退化为"无可比文献"。"""
    class _BoomLLM:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("api down")

    with pytest.raises(RuntimeError):
        extract_records(_BoomLLM(), [])


def _draft_stub():
    from drpaper.paper.draft import Draft

    draft = Draft()
    draft.begin("标题", [])
    return draft
