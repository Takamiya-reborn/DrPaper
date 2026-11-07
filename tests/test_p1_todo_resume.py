"""P1-4 验证：任务清单持久化与断点续跑（离线，假流式客户端驱动真实 ToolBox）。

对应 plan.md P1-4：
- update_todo 把步骤状态（pending / in_progress / completed）写入磁盘 JSON
- 模型可经工具读写清单；每次工具调用后状态变更即时落盘
- 中断后重启：新 PaperAgent 从磁盘恢复草稿/文献库/清单，
  系统提示词注入断点恢复块，未确认门禁状态一并恢复，不重做已完成步骤
- 快照损坏或缺失时正常重新开始
"""

from __future__ import annotations

import json

from fake_llm import agent_with, chunk, tool_call

from drpaper.agent.core import PaperAgent

_SESSION = "session.json"


def _read_session(tmp_path) -> dict:
    return json.loads((tmp_path / _SESSION).read_text(encoding="utf-8"))


def _todo_steps() -> list[dict]:
    return [
        {"content": "调研检索增强生成相关文献", "status": "completed"},
        {"content": "提交提纲并请求确认", "status": "in_progress"},
        {"content": "逐节撰写正文", "status": "pending"},
        {"content": "导出交付", "status": "pending"},
    ]


# ---- 清单读写与持久化 ----


def test_update_todo_persists_and_read_roundtrip(tmp_path) -> None:
    agent = agent_with([], tmp_path)
    handlers = agent._toolbox.handlers()

    result = json.loads(handlers["update_todo"]({"steps": _todo_steps()}))
    assert result["status"] == "ok"
    assert result["progress"] == "1/4 步已完成"

    # 立即落盘：清单 + 状态原样进磁盘 JSON
    saved = _read_session(tmp_path)
    assert saved["todo"] == _todo_steps()

    # read_todo 从会话状态读回
    result = json.loads(handlers["read_todo"]({}))
    assert result["steps"] == _todo_steps()
    assert result["progress"] == "1/4 步已完成"


def test_update_todo_rejects_invalid(tmp_path) -> None:
    agent = agent_with([], tmp_path)
    handlers = agent._toolbox.handlers()
    for bad in ([], [{"content": "", "status": "pending"}],
                [{"content": "调研", "status": "done"}], "不是列表"):
        result = json.loads(handlers["update_todo"]({"steps": bad}))
        assert "error" in result, f"应拒绝非法输入: {bad}"
    assert agent._toolbox.todo.steps == []
    assert not (tmp_path / _SESSION).exists() or _read_session(tmp_path)["todo"] == []


def test_every_tool_call_persists_snapshot(tmp_path) -> None:
    """状态变更类工具调用后即时落盘，无需显式保存动作。"""
    agent = agent_with(
        [
            [chunk(tool_calls=[tool_call("begin_draft", {
                "title": "断点续跑测试",
                "sections": [{"name": "1 引言", "budget": 100}],
            })], finish="tool_calls")],
            [chunk(content="请确认提纲。", finish="stop")],
        ],
        tmp_path,
    )
    agent.chat("帮我写篇测试论文")
    saved = _read_session(tmp_path)
    assert saved["draft"]["title"] == "断点续跑测试"
    assert saved["draft"]["sections"][0]["name"] == "1 引言"
    assert saved["outline_confirmed"] is False


# ---- 断点续跑 ----


def _outline_rounds() -> list[list]:
    return [
        [chunk(tool_calls=[tool_call("begin_draft", {
            "title": "断点续跑测试",
            "sections": [{"name": "1 引言", "budget": 100}, {"name": "2 方法", "budget": 100}],
        })], finish="tool_calls")],
        [chunk(content="请确认提纲。", finish="stop")],
    ]


def _confirm_and_write_rounds() -> list[list]:
    return [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("submit_section", {"name": "1 引言", "content": "研" * 100})], finish="tool_calls")],
        [chunk(tool_calls=[tool_call("update_todo", {"steps": [
            {"content": "提交提纲并确认", "status": "completed"},
            {"content": "写完 2 方法", "status": "pending"},
        ]})], finish="tool_calls")],
        [chunk(content="第 1 节完成，下次继续。", finish="stop")],
    ]


def test_resume_restores_state_and_prompt(tmp_path) -> None:
    # 第一次会话：提纲确认 + 第 1 节完成 + 清单更新（随后进程"被杀"）
    agent = agent_with(_outline_rounds(), tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.llm._client.chat.completions.rounds = _confirm_and_write_rounds()
    agent.chat("可以，开始写")
    assert _read_session(tmp_path)["outline_confirmed"] is True

    # 重启：全新 PaperAgent 读同一 output_dir
    agent2 = agent_with(
        [[chunk(tool_calls=[tool_call("submit_section", {"name": "2 方法", "content": "法" * 100})], finish="tool_calls")],
         [chunk(content="全部完成。", finish="stop")]],
        tmp_path,
    )
    assert agent2.resumed is True
    system = agent2.messages[0]["content"]
    assert "断点恢复" in system
    assert "- [x] 提交提纲并确认" in system  # 清单视图进提示词
    assert "禁止重做" in system
    assert "已完成 1 节" in system

    # 草稿与门禁状态一并恢复：无需重新 begin_draft / confirm_outline 即可续写
    assert agent2._toolbox.draft.get_section("1 引言").content == "研" * 100
    agent2.chat("继续")
    saved = _read_session(tmp_path)
    assert saved["draft"]["sections"][1]["content"] == "法" * 100
    assert saved["outline_confirmed"] is True


def test_resume_notice_for_cli(tmp_path) -> None:
    agent = agent_with([], tmp_path)
    assert agent.resume_notice is None  # 全新会话无提示
    agent._toolbox.todo.replace(_todo_steps())
    agent._toolbox.store.save(agent._toolbox.snapshot())

    agent2 = agent_with([], tmp_path)
    notice = agent2.resume_notice
    assert notice is not None and "1/4" in notice and "继续" in notice


def test_all_completed_todo_no_resume_block(tmp_path) -> None:
    """清单全部完成：状态照常恢复，但不再注入断点提示。"""
    agent = agent_with([], tmp_path)
    agent._toolbox.todo.replace([{"content": "已完成的一步", "status": "completed"}])
    agent._toolbox.store.save(agent._toolbox.snapshot())

    agent2 = agent_with([], tmp_path)
    assert agent2.resumed is False
    assert "上次会话未完成" not in agent2.messages[0]["content"]
    assert agent2._toolbox.todo.total == 1  # 状态仍已恢复


# ---- 容错 ----


def test_missing_session_starts_fresh(tmp_path) -> None:
    agent = agent_with([], tmp_path)
    assert agent.resumed is False
    assert "上次会话未完成" not in agent.messages[0]["content"]


def test_corrupt_session_starts_fresh(tmp_path) -> None:
    (tmp_path / _SESSION).write_text("{不是JSON", encoding="utf-8")
    agent = agent_with([], tmp_path)
    assert agent.resumed is False
    assert agent._toolbox.draft.title == ""
