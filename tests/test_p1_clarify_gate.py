"""P1-1 验证：提纲确认硬门禁（离线，假流式客户端驱动真实 ToolBox）。

对应 plan.md P1-1：
- begin_draft 后提纲待确认，submit_section 被拒绝
- 同一轮内模型不得自行 confirm_outline（无用户回应即拒绝）
- 用户回应后 confirm_outline 解锁写作
- 重新 begin_draft 重置确认状态
"""

from __future__ import annotations

from fake_llm import agent_with, chunk, tool_call, tool_results


def _tool_results(messages: list[dict]) -> list[dict]:
    return tool_results(messages)


def _begin_rounds() -> list[list]:
    return [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls")],
        [chunk(content="以上是提纲，请确认。", finish="stop")],
    ]


def test_submit_section_before_confirmation_rejected(tmp_path) -> None:
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")
    results = _tool_results(agent.messages)
    assert results[0]["status"] == "pending_confirmation"  # begin_draft 进入待确认态
    assert not agent.draft.sections[0].content

    agent.llm._client.chat.completions.rounds = [
        # 跳过确认直接写 → 被门禁拒绝
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 100})], finish="tool_calls")],
        [chunk(content="好的，等待确认。", finish="stop")],
    ]
    agent.chat("别等了，直接把第一节写了")
    results = _tool_results(agent.messages)
    assert "提纲未经用户确认" in results[-1]["error"]
    assert not agent.draft.sections[0].content


def test_confirm_outline_without_user_reply_rejected(tmp_path) -> None:
    """提纲提交后的同一轮内模型试图自行确认 → 结构性拒绝。"""
    rounds = _begin_rounds()[:-1] + [  # 去掉最终文本轮，同一轮内继续调工具
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(content="明白了，等待用户回复。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")
    results = _tool_results(agent.messages)
    assert "尚未提交提纲" not in results[1].get("error", "")  # 提纲确已提交
    assert "用户尚未对提纲作出回应" in results[1]["error"]
    assert not agent._toolbox._outline_confirmed


def test_user_reply_then_confirm_unlocks_writing(tmp_path) -> None:
    rounds = _begin_rounds()
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")

    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 100})], finish="tool_calls")],
        [chunk(content="第 1 节完成。", finish="stop")],
    ]
    reply = agent.chat("可以，开始写")
    assert reply == "第 1 节完成。"
    assert agent.draft.sections[0].content == "研" * 100


def test_rebegin_resets_confirmation(tmp_path) -> None:
    """已确认后重新 begin_draft（大改动）→ 回到待确认态。"""
    rounds = _begin_rounds()
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")

    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(content="已确认。", finish="stop")],
    ]
    agent.chat("可以")
    assert agent._toolbox._outline_confirmed

    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "修改后的题目",
            "sections": [{"name": "1 引言", "budget": 200}],
        })], finish="tool_calls")],
        [chunk(content="新提纲请确认。", finish="stop")],
    ]
    agent.chat("把引言预算改到 200")
    assert not agent._toolbox._outline_confirmed  # 确认被重置

    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 200})], finish="tool_calls")],
        [chunk(content="等待确认。", finish="stop")],
    ]
    agent.chat("（模型误开写）")
    results = _tool_results(agent.messages)
    assert "提纲未经用户确认" in results[-1]["error"]


def test_confirm_without_outline_rejected(tmp_path) -> None:
    rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(content="好的，先提交提纲。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("确认提纲")
    results = _tool_results(agent.messages)
    assert "尚未提交提纲" in results[0]["error"]
