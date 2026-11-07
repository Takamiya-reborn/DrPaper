"""P1-2 验证：章节写作拆分为子代理（离线，假流式客户端驱动真实 ToolBox）。

对应 plan.md P1-2：
- 主循环新增 research_topic / write_section / review_draft，整节正文经写作子代理落库
- 主上下文只持有提纲与各节摘要，整节正文与检索详情不进主对话
- 子代理独立上下文：各节共用同一份写作系统提示词（风格一致的机制来源）
- 子代理未交回结构化结果时，失败原样回传主循环
"""

from __future__ import annotations

import json

from fake_llm import agent_with, chunk, tool_call, tool_results

from drpaper.agents.writer.agent import SYSTEM as WRITER_SYSTEM
from drpaper.literature.base import Paper

SECTION_CONTENT = "研" * 200
SECTIONS = [{"name": f"{i} 节", "budget": 200} for i in range(1, 6)]


class _StubProvider:
    """离线检索源：返回两条固定假文献，避免测试联网。"""

    name = "stub"
    warnings: list[str] = []

    def search(self, query: str, max_results: int) -> list[Paper]:
        return [
            Paper(
                uid=f"stub:{query}:{i}",
                title=f"Stub paper {i} on {query}",
                authors=["Author A"],
                year=2024,
                abstract=f"Placeholder abstract about {query}.",
                url="https://example.com",
            )
            for i in range(min(2, max_results))
        ]


def _tool_results(messages: list[dict]) -> list[dict]:
    return tool_results(messages)


def _main_text(agent_messages: list[dict]) -> str:
    """主循环历史的整体序列化（正文、tool_calls 参数与结果全算主上下文）。"""
    return json.dumps(agent_messages, ensure_ascii=False)


def _outline_rounds(sections: list[dict]) -> list[list]:
    return [
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "begin_draft", {"title": "测试论文", "sections": sections}
                    )
                ],
                finish="tool_calls",
            )
        ],
        [chunk(content="以上是提纲，请确认。", finish="stop")],
    ]


def _confirmed_rounds(sections: list[dict]) -> list[list]:
    """提纲提交 + 用户确认解锁写作的完整前缀。"""
    return _outline_rounds(sections) + [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
    ]


def test_research_returns_digest_cards_only(tmp_path) -> None:
    """调研子代理登记文献入库，主对话只拿资料卡（不含摘要等检索详情）。"""
    rounds = [
        [
            chunk(
                tool_calls=[tool_call("research_topic", {"topic": "大模型幻觉"})],
                finish="tool_calls",
            )
        ],
        [
            chunk(
                tool_calls=[
                    tool_call("search_literature", {"query": "llm hallucination"})
                ],
                finish="tool_calls",
            )
        ],
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "submit_research",
                        {
                            "summary": "已有较多缓解研究。",
                            "findings": [
                                {
                                    "index": 1,
                                    "title": "Stub paper 0 on llm hallucination",
                                    "key_point": "提出缓解方法",
                                }
                            ],
                        },
                    )
                ],
                finish="tool_calls",
            )
        ],
        [chunk(content="调研完成。", finish="stop")],
        [chunk(content="资料卡已就绪。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent._toolbox.provider = _StubProvider()  # 离线假文献
    agent.chat("帮我调研大模型幻觉")
    results = _tool_results(agent.messages)
    assert results[0]["status"] == "ok"
    assert results[0]["findings"][0]["index"] == 1
    assert agent._toolbox.research_notes[0]["key_point"] == "提出缓解方法"
    assert len(agent.draft.references) == 2  # 文献已登记共享文献库
    # 检索详情（截断摘要）只留在子代理上下文，不进主对话
    assert "Placeholder abstract" not in _main_text(agent.messages)


def test_write_section_delegates_and_main_holds_digest(tmp_path) -> None:
    rounds = _outline_rounds([{"name": "1 引言", "budget": 100}]) + [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [
            chunk(
                tool_calls=[tool_call("write_section", {"name": "1 引言"})],
                finish="tool_calls",
            )
        ],
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "save_section",
                        {
                            "name": "1 引言",
                            "content": "研" * 100,
                            "digest": "引言概述研究背景。",
                        },
                    )
                ],
                finish="tool_calls",
            )
        ],
        [chunk(content="该节已完成。", finish="stop")],
        [chunk(content="第 1 节完成。", finish="stop")],
    ]
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.chat("可以，开始写")
    assert agent.draft.sections[0].content == "研" * 100  # 子代理已写入草稿
    assert agent._toolbox.section_digests["1 引言"] == "引言概述研究背景。"
    result = _tool_results(agent.messages)[-1]
    assert result["status"] == "ok" and result["digest"] == "引言概述研究背景。"
    # 整节正文不进主对话
    assert "研" * 100 not in _main_text(agent.messages)


def test_write_section_requires_confirmation(tmp_path) -> None:
    """提纲未确认时 write_section 与 submit_section 同受门禁约束。"""
    agent = agent_with(_outline_rounds([{"name": "1 引言", "budget": 100}]), tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.llm._client.chat.completions.rounds = [
        [
            chunk(
                tool_calls=[tool_call("write_section", {"name": "1 引言"})],
                finish="tool_calls",
            )
        ],
        [chunk(content="等待确认。", finish="stop")],
    ]
    agent.chat("直接开写")
    result = _tool_results(agent.messages)[-1]
    assert "提纲未经用户确认" in result["error"]
    assert not agent.draft.sections[0].content


def test_writer_system_prompt_identical_across_sections(tmp_path) -> None:
    """各节写作子代理共用同一份系统提示词（风格一致的机制来源）。"""
    rounds = _confirmed_rounds(SECTIONS)
    for i in range(1, 6):
        name = f"{i} 节"
        rounds += [
            [
                chunk(
                    tool_calls=[tool_call("write_section", {"name": name})],
                    finish="tool_calls",
                )
            ],
            [
                chunk(
                    tool_calls=[
                        tool_call(
                            "save_section",
                            {
                                "name": name,
                                "content": SECTION_CONTENT,
                                "digest": f"第 {i} 节摘要。",
                            },
                        )
                    ],
                    finish="tool_calls",
                )
            ],
            [chunk(content="该节已完成。", finish="stop")],
        ]
    rounds.append([chunk(content="全部完成。", finish="stop")])
    agent = agent_with(rounds, tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.chat("可以，开始写")
    assert all(s.content == SECTION_CONTENT for s in agent.draft.sections)

    writer_systems = {
        req[0]["content"]
        for req in agent.llm._client.chat.completions.requests
        if any(
            call["function"]["name"] == "save_section"
            for msg in req
            for call in msg.get("tool_calls") or []
        )
    }
    assert writer_systems == {WRITER_SYSTEM}  # 每节都是同一份写作提示词
    main_system = agent.messages[0]["content"]
    assert WRITER_SYSTEM not in main_system  # 主循环提示词不掺写作提示词


def test_main_context_shrinks_vs_inline_submission(tmp_path) -> None:
    """同一 5 节内容：子代理路径的主上下文增量明显小于主循环直接提交路径（拆分前等价）。

    对比的是「提纲确认之后」到「5 节写完」的主历史增量——排掉两边相同的
    系统提示词等固定开销，剩下的差异就是整节正文进不进主对话。
    """
    content = "研" * 800  # 真实节规模
    sections = [{"name": f"{i} 节", "budget": 800} for i in range(1, 6)]

    def _run(rounds: list[list]) -> tuple[int, int]:
        agent = agent_with(rounds, tmp_path)
        agent.chat("帮我写篇测试论文")
        before = len(_main_text(agent.messages))
        agent.chat("可以，开始写")
        return len(_main_text(agent.messages)) - before

    new_rounds = _confirmed_rounds(sections)
    for i in range(1, 6):
        name = f"{i} 节"
        new_rounds += [
            [
                chunk(
                    tool_calls=[tool_call("write_section", {"name": name})],
                    finish="tool_calls",
                )
            ],
            [
                chunk(
                    tool_calls=[
                        tool_call(
                            "save_section",
                            {
                                "name": name,
                                "content": content,
                                "digest": f"第 {i} 节摘要。",
                            },
                        )
                    ],
                    finish="tool_calls",
                )
            ],
            [chunk(content="该节已完成。", finish="stop")],
        ]
    new_rounds.append([chunk(content="全部完成。", finish="stop")])

    old_rounds = _confirmed_rounds(sections)
    for i in range(1, 6):
        old_rounds.append(
            [
                chunk(
                    tool_calls=[
                        tool_call(
                            "submit_section",
                            {
                                "name": f"{i} 节",
                                "content": content,
                            },
                        )
                    ],
                    finish="tool_calls",
                )
            ]
        )
    old_rounds.append([chunk(content="全部完成。", finish="stop")])

    new_delta, old_delta = _run(new_rounds), _run(old_rounds)
    assert new_delta < old_delta * 0.5  # 正文不再进主对话，增量只剩摘要与进度
    assert old_delta - new_delta > 4 * len(content)  # 差值 ≈ 5 份正文


def test_writer_failure_fed_back(tmp_path) -> None:
    """写作子代理未提交正文时，失败原因原样回传主循环。"""
    agent = agent_with(_outline_rounds([{"name": "1 引言", "budget": 100}]), tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [
            chunk(
                tool_calls=[tool_call("write_section", {"name": "1 引言"})],
                finish="tool_calls",
            )
        ],
        # 写作子代理只输出文本，不调 save_section → 失败
        [chunk(content="我不知道怎么写。", finish="stop")],
        [chunk(content="写作失败了。", finish="stop")],
    ]
    agent.chat("可以，开始写")
    result = _tool_results(agent.messages)[-1]
    assert "写作子代理未提交正文" in result["error"]
    assert not agent.draft.sections[0].content


def test_review_draft_returns_issues(tmp_path) -> None:
    """审校子代理返回结构化意见列表；格式不合法时在子代理 loop 内自纠。"""
    agent = agent_with(_outline_rounds([{"name": "1 引言", "budget": 100}]), tmp_path)
    agent.chat("帮我写篇测试论文")
    agent.llm._client.chat.completions.rounds = [
        [chunk(tool_calls=[tool_call("confirm_outline", {})], finish="tool_calls")],
        [
            chunk(
                tool_calls=[tool_call("write_section", {"name": "1 引言"})],
                finish="tool_calls",
            )
        ],
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "save_section",
                        {
                            "name": "1 引言",
                            "content": "研" * 100,
                            "digest": "引言概述研究背景。",
                        },
                    )
                ],
                finish="tool_calls",
            )
        ],
        [chunk(content="该节已完成。", finish="stop")],
        [chunk(tool_calls=[tool_call("review_draft", {})], finish="tool_calls")],
        # 第一次提交缺 severity → 被拒绝
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "submit_review",
                        {
                            "summary": "整体尚可。",
                            "issues": [
                                {
                                    "section": "1 引言",
                                    "problem": "论证薄弱",
                                    "suggestion": "补充文献支撑",
                                }
                            ],
                        },
                    )
                ],
                finish="tool_calls",
            )
        ],
        # 自纠后重新提交
        [
            chunk(
                tool_calls=[
                    tool_call(
                        "submit_review",
                        {
                            "summary": "整体尚可。",
                            "issues": [
                                {
                                    "severity": "high",
                                    "section": "1 引言",
                                    "problem": "论证薄弱",
                                    "suggestion": "补充文献支撑",
                                }
                            ],
                        },
                    )
                ],
                finish="tool_calls",
            )
        ],
        [chunk(content="审校完成。", finish="stop")],
        [chunk(content="审校意见已整理。", finish="stop")],
    ]
    agent.chat("可以，开始写，写完审校一遍")
    result = _tool_results(agent.messages)[-1]
    assert result["status"] == "ok"
    assert result["issues"] == [
        {
            "severity": "high",
            "section": "1 引言",
            "problem": "论证薄弱",
            "suggestion": "补充文献支撑",
        }
    ]
    # 拒绝与自纠都发生在子代理上下文，主对话只见最终意见
    assert "severity 必须是" not in _main_text(agent.messages)
