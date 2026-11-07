"""P1-3 验证：Workflow 文档化（离线，假流式客户端驱动真实 ToolBox）。

对应 plan.md P1-3：
- 内置流程文档完整：阶段链、验收标准、入口工具齐备
- 进入新阶段时阶段文档随工具结果注入一次；同阶段重入 / 回退不重复注入
- 注入的文档真实进入模型上下文（第二次请求可见）
- 只改 Markdown（用户目录覆盖内置）即可调整流程，不改任何 Python 代码
"""

from __future__ import annotations

import shutil

from fake_llm import agent_with, chunk, tool_call

from drpaper.workflows import WorkflowManager

_STAGE_CHAIN = ["需求确认", "文献调研", "提纲与确认", "逐节写作", "审校评估", "导出交付"]


def _tool_texts(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "tool"]


def _begin_rounds() -> list[list]:
    return [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "测试论文",
            "sections": [{"name": "1 引言", "budget": 100}],
        })], finish="tool_calls")],
        [chunk(content="以上是提纲，请确认。", finish="stop")],
    ]


def test_builtin_workflow_complete() -> None:
    manager = WorkflowManager()
    workflow = next(w for w in manager.workflows if w.name == "paper-drafting")
    assert [s.name for s in workflow.stages] == _STAGE_CHAIN
    for stage in workflow.stages:
        assert stage.entry_tools, f"{stage.name} 缺少入口工具"
        assert "## 验收标准" in stage.body, f"{stage.name} 缺少验收标准"
        assert "## 可用工具" in stage.body, f"{stage.name} 缺少可用工具"
    index = manager.index_prompt()
    assert "paper-drafting" in index
    for name in _STAGE_CHAIN:
        assert name in index


def test_stage_doc_injected_on_first_entry(tmp_path) -> None:
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")
    results = _tool_texts(agent.messages)
    assert any("【workflow：paper-drafting】" in r for r in results)  # 首次启用带总则
    injected = next(r for r in results if "【当前阶段：提纲与确认】" in r)
    assert "## 验收标准" in injected
    assert "begin_draft" in injected  # 文档中的可用工具说明
    # JSON 结果在前、流程文档在后，两者同在一条 tool 消息里
    assert injected.lstrip().startswith("{")


def test_no_reinject_on_same_stage_or_earlier(tmp_path) -> None:
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")

    # 同阶段重入（重新 begin_draft）→ 不重复注入
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "修改后的题目",
            "sections": [{"name": "1 引言", "budget": 200}],
        })], finish="tool_calls")],
        [chunk(content="新提纲请确认。", finish="stop")],
    ]
    agent.chat("把引言预算改到 200")
    assert not any("【当前阶段" in r for r in _tool_texts(agent.messages)[-1:])

    # 未确认就尝试写作 → 报错结果同样不注入（阶段未前进）
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 100})], finish="tool_calls")],
        [chunk(content="等待确认。", finish="stop")],
    ]
    agent.chat("（模型误开写）")
    results = _tool_texts(agent.messages)
    assert "提纲未经用户确认" in results[-1]


def test_stage_advances_after_confirmation(tmp_path) -> None:
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 100})], finish="tool_calls")],
        [chunk(content="第 1 节完成。", finish="stop")],
    ]
    agent.chat("可以，开始写")
    results = _tool_texts(agent.messages)
    # confirm_outline 与提纲同阶段：不注入；submit_section 进入写作阶段：注入
    assert "【当前阶段" not in results[-2]
    assert "【当前阶段：逐节写作】" in results[-1]


def test_unmapped_tool_no_injection(tmp_path) -> None:
    rounds = [
        [chunk(tool_calls=[tool_call("load_skill", {"name": "paper-review"})], finish="tool_calls")],
        [chunk(content="已加载。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("诊断一下论文该怎么改")
    results = _tool_texts(agent.messages)
    assert results and not any("【当前阶段" in r or "【workflow" in r for r in results)


def test_injected_doc_reaches_model_context(tmp_path) -> None:
    agent = agent_with(_begin_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")
    requests = agent.llm._client.chat.completions.requests
    assert len(requests) >= 2
    second_round = requests[1]
    assert any(
        m.get("role") == "tool" and "【当前阶段：提纲与确认】" in m["content"]
        for m in second_round
    )


def test_markdown_edit_takes_effect_without_code(tmp_path) -> None:
    """复制内置文档 → 只改 Markdown 验收标准 → 不改任何 Python 代码即生效。"""
    from drpaper.workflows import manager as manager_mod

    source = manager_mod.Path(manager_mod.__file__).parent / "builtin" / "paper-drafting"
    override_root = tmp_path / "workflows"
    override_root.mkdir()
    shutil.copytree(source, override_root / "paper-drafting")

    criterion = "- 向用户展示提纲时，回复必须以「【提纲待确认】」开头"
    outline = override_root / "paper-drafting" / "stages" / "03-outline.md"
    text = outline.read_text(encoding="utf-8")
    text = text.replace("## 可用工具", f"{criterion}\n\n## 可用工具")
    outline.write_text(text, encoding="utf-8")

    # 内置版不含新验收标准，覆盖版含——同一机制，纯文档差异
    assert criterion not in WorkflowManager().enter_doc("begin_draft")
    agent = agent_with(_begin_rounds(), tmp_path)
    agent._toolbox.workflows = WorkflowManager(user_dir=override_root)
    agent._toolbox.workflow_index = agent._toolbox.workflows.index_prompt()
    agent.chat("帮我写篇测试论文")
    injected = next(
        r for r in _tool_texts(agent.messages) if "【当前阶段：提纲与确认】" in r
    )
    assert criterion in injected
