"""P2-1 验证：上下文压缩（离线，假流式客户端驱动真实 chat 循环与 Compactor）。

对应 plan.md P2-1：
- 监控上下文长度：实测用量优先，无实测时按消息体量估算；超过阈值（窗口 × 70%）
  在下一轮请求前触发一次摘要调用，把旧历史就地折叠为单条消息
- 摘要保留：用户明确要求进摘要转写；提纲/确认状态/清单/文献编号对照以
  结构化状态快照随摘要注入（不依赖模型转述）
- 折叠边界不拆散 assistant.tool_calls 与其 tool 结果
- 主循环与子代理共用压缩器；摘要调用失败时放弃本轮压缩，任务继续
"""

from __future__ import annotations

import json

from fake_llm import agent_with, chunk, tool_call, tool_results, usage

from drpaper.agent.compaction import Compactor, context_window, estimate_tokens
from drpaper.literature.base import Paper


def _pad(agent, rounds: int = 4) -> None:
    """往主历史里垫几轮长对话，制造可折叠的旧历史。"""
    for i in range(rounds):
        agent.messages.append({"role": "user", "content": f"第{i}轮补充：{'细节' * 150}"})
        agent.messages.append({"role": "assistant", "content": "已记录。"})


def _begin_rounds() -> list[list]:
    return [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "压缩测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls")],
        [chunk(content="以上是提纲，请确认。", finish="stop")],
    ]


def _pairs_intact(messages: list[dict]) -> None:
    """保留区里每条 tool 结果都有配对的 assistant.tool_calls。"""
    ids = {
        call["id"]
        for m in messages
        for call in (m.get("tool_calls") or [])
    }
    orphans = [
        m["tool_call_id"]
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id") not in ids
    ]
    assert not orphans, f"存在悬空 tool 结果: {orphans}"


# ---- 触发机制 ----


def test_no_compaction_below_threshold(tmp_path) -> None:
    """默认窗口下短会话不触发：无摘要调用，历史原样保留。"""
    agent = agent_with([[chunk(content="你好。", finish="stop")]], tmp_path)
    reply = agent.chat("你好")
    assert reply == "你好。"
    assert agent._compactor.compactions == 0
    requests = agent.llm._client.chat.completions.requests
    assert len(requests) == 1  # 只有模型轮，无额外摘要调用
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant"]


def test_estimate_path_triggers_without_usage() -> None:
    """prompt_tokens=0（无实测）时按消息体量估算触发；无可折叠历史时放弃。"""
    comp = Compactor(llm=_noop_llm(), window_tokens=1, threshold=0.7)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
    ]
    assert comp.before_round(messages, 0) is True  # 估算超线 → 折叠
    assert "压缩摘要" in messages[1]["content"]
    short = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert comp.before_round(short, 0) is False  # 没有可安全折叠的历史


def test_compaction_then_loop_continues(tmp_path) -> None:
    """实测用量超线触发压缩：折叠后 loop 照常继续（提纲提交、确认、写作流转）。"""
    agent = agent_with([], tmp_path)
    base = estimate_tokens(agent.messages)
    # 触发线高于「系统 + 垫高历史」的估算基线：光靠估算永不触发
    agent._compactor.window_tokens = (base + 2000) * 10 // 7
    _pad(agent)
    agent.llm._client.chat.completions.rounds = [
        # it1 请求 begin_draft（实测用量超线）
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "压缩测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls", usage=usage(base + 2500))],
        # it2 请求前触发压缩：摘要轮
        [chunk(content="摘要：此前讨论了写作要求。", finish="stop", usage=usage(10))],
        # 折叠后的模型请求正常返回
        [chunk(content="以上是提纲，请确认。", finish="stop", usage=usage(10))],
    ]
    agent.chat("帮我写一篇测试论文")

    assert agent._compactor.compactions == 1
    assert agent.draft.title == "压缩测试论文"  # begin_draft 照常执行，工具流转未受影响
    folded = agent.messages[1]
    assert "压缩摘要" in folded["content"]
    assert "此前讨论了写作要求" in folded["content"]
    _pairs_intact(agent.messages)

    # 折叠后第二个回合照常走确认门禁（真实用户输入解锁）
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls", usage=usage(10))],
        [chunk(content="开始写作。", finish="stop", usage=usage(10))],
    ]
    agent.chat("可以，开始写")
    assert agent._toolbox._outline_confirmed is True
    assert agent._compactor.compactions == 1  # 实测用量已回落，不再压缩


def test_no_compaction_while_usage_below_threshold(tmp_path) -> None:
    """实测用量未超线就不压缩——即使垫高历史推高估算（实测优先）。"""
    agent = agent_with([], tmp_path)
    base = estimate_tokens(agent.messages)
    agent._compactor.window_tokens = (base + 2000) * 10 // 7  # 触发线 = base+1400
    _pad(agent)  # 垫高后估算 ≈ base+600，仍低于触发线
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "压缩测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls", usage=usage(base))],
        [chunk(content="以上是提纲，请确认。", finish="stop", usage=usage(base))],
    ]
    agent.chat("帮我写篇测试论文")
    assert agent._compactor.compactions == 0  # 实测 base < 触发线 base+1400
    assert "压缩摘要" not in json.dumps(agent.messages, ensure_ascii=False)


def test_summary_call_receives_history_and_requirements(tmp_path) -> None:
    """摘要调用收到被折叠的完整转写，用户原话要求在其中（保留要求的输入侧保证）。"""
    requirement = "用户要求：全文自称必须用「本文」，禁止用「我们」。" + "其他要求。" * 50
    agent = agent_with([[chunk(content="好的。", finish="stop")]], tmp_path)
    agent._compactor.window_tokens = 1  # 必然触发
    # 要求先说，随后垫足够多的消息把它推出保留区，进入被折叠区域
    agent.messages.append({"role": "user", "content": requirement})
    agent.messages.append({"role": "assistant", "content": "已明确要求。"})
    _pad(agent, rounds=5)
    agent.llm._client.chat.completions.rounds = [
        [chunk(content="摘要。", finish="stop")],
        [chunk(content="已折叠。", finish="stop")],
    ]
    agent.chat("继续")

    requests = agent.llm._client.chat.completions.requests
    summary_req = requests[0]
    assert "对话压缩器" in summary_req[0]["content"]  # 摘要指令带保留要求
    transcript = summary_req[1]["content"]
    assert requirement in transcript  # 用户要求原样进入摘要转写
    assert "第0轮补充" in transcript


def test_summary_failure_skips_compaction(tmp_path) -> None:
    """摘要调用持续空回复 → 放弃本轮压缩，任务照常继续。"""
    agent = agent_with([
        [chunk(content="", finish="stop")],  # 摘要尝试 1：空
        [chunk(content="", finish="stop")],  # 摘要尝试 2：空
        [chunk(content="正常回复。", finish="stop")],
    ], tmp_path)
    agent._compactor.window_tokens = 1
    _pad(agent)
    reply = agent.chat("继续")
    assert reply == "正常回复。"
    assert agent._compactor.compactions == 0
    assert "压缩摘要" not in json.dumps(agent.messages, ensure_ascii=False)


# ---- 折叠边界安全性（直接单测 Compactor）----


def _noop_llm():
    """始终返回固定摘要的假客户端（FakeStreamClient 只剩一轮时无限重复）。"""
    from drpaper.llm.client import LLMClient
    from fake_llm import FakeStreamClient

    llm = LLMClient(api_key="test", base_url="", model="test-model")
    llm._client = FakeStreamClient([[chunk(content="摘要。", finish="stop")]])
    return llm


def test_boundary_never_splits_tool_pairs(tmp_path) -> None:
    """边界落在 tool 消息上时回退到配对 assistant 之前，tool 结果永不悬空。"""
    comp = Compactor(llm=_noop_llm(), window_tokens=1, threshold=0.7)
    base = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    for keep in (1, 2, 3):  # 三个边界位置：assistant / user / tool（回退）
        messages = [dict(m) for m in base]
        comp.keep_recent = keep
        assert comp.compact(messages) is True, f"keep_recent={keep}"
        assert messages[0]["role"] == "system"  # 系统提示词永不折叠
        assert "压缩摘要" in messages[1]["content"]
        _pairs_intact(messages)


# ---- 摘要保留：结构化状态快照 ----


def test_snapshot_preserves_confirmed_outline_todo_and_refs(tmp_path) -> None:
    """压缩后不遗忘已确认的提纲与写作要求：清单、提纲确认、大纲预算、
    文献编号对照以权威状态块随摘要注入，不依赖模型转述。"""
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写一篇 800 字的测试论文，全文自称用「本文」")
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(content="开始写。", finish="stop")],
    ]
    agent.chat("可以，开始写")
    assert agent._toolbox._outline_confirmed is True
    agent._toolbox.handlers()["set_target_chars"]({"target_chars": 800})
    agent._toolbox.todo.replace([
        {"content": "写完正文", "status": "completed"},
        {"content": "导出交付", "status": "pending"},
    ])
    agent.draft.add_papers([
        Paper(uid="s1", title="Hallucination Survey 2024", authors=["A"], year=2024,
              abstract="", url=""),
    ])

    _pad(agent, rounds=5)
    agent._compactor.window_tokens = 1
    agent.llm._client.chat.completions.rounds = [[chunk(content="对话摘要。", finish="stop")]]
    assert agent._compactor.compact(agent.messages) is True

    folded = agent.messages[1]["content"]
    assert "权威状态" in folded
    assert "提纲已确认" in folded  # 确认状态机制性保留
    assert "800 字" in folded  # 目标字数保留
    assert "1 引言" in folded and "100 字" in folded  # 大纲与预算保留
    assert "- [x] 写完正文" in folded and "- [ ] 导出交付" in folded  # 清单视图
    assert "[1] Hallucination Survey 2024" in folded  # 文献编号对照
    assert "对话摘要。" in folded  # 模型生成的摘要也在
    _pairs_intact(agent.messages)


# ---- 装配 ----


def test_agent_wires_shared_compactor(tmp_path) -> None:
    """PaperAgent 默认装配压缩器：主循环与 ToolBox（子代理）共用同一实例。"""
    agent = agent_with([], tmp_path)
    assert agent._toolbox.compactor is agent._compactor
    assert agent._compactor.state_snapshot == agent._toolbox.state_block
    assert agent._compactor.window_tokens == context_window()
    assert agent._compactor.threshold == 0.7


def test_subagents_receive_shared_compactor(tmp_path) -> None:
    """写作子代理的 loop 中压缩器被调用，且作用在子代理自己的历史上。"""
    calls: list[tuple[list, int]] = []

    class _Recording:
        def before_round(self, messages, prompt_tokens):
            calls.append((messages, prompt_tokens))
            return False

    agent = agent_with(_begin_rounds() + [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("write_section", {"name": "1 引言"})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("save_section", {
            "name": "1 引言", "content": "研" * 100, "digest": "引言概述背景。",
        })], finish="tool_calls")],
        [chunk(content="该节完成。", finish="stop")],
        [chunk(content="第 1 节完成。", finish="stop")],
    ], tmp_path)
    agent._toolbox.compactor = _Recording()
    agent.chat("帮我写篇测试论文")
    agent.chat("可以，开始写")

    assert agent.draft.sections[0].content == "研" * 100  # 子代理正常完成任务
    subagent_calls = [
        msgs for msgs, _ in calls
        if msgs and "你负责撰写" in json.dumps(msgs, ensure_ascii=False)
    ]
    assert subagent_calls  # 压缩器确实进入了子代理 loop


def test_compaction_tool_results_flow(tmp_path) -> None:
    """压缩发生时工具结果照常流转：折叠不影响当轮 tool_call 的执行与回填。"""
    agent = agent_with(_begin_rounds(), tmp_path)
    agent._compactor.window_tokens = 1
    _pad(agent)
    agent.llm._client.chat.completions.rounds.insert(0, [
        chunk(content="摘要。", finish="stop")
    ])
    agent.chat("帮我写篇测试论文")
    results = tool_results(agent.messages)
    assert results[0]["status"] == "pending_confirmation"  # begin_draft 正常执行
