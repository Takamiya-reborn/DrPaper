"""P1-2 验收：章节写作拆分为子代理，主上下文 token 明显下降（真实 LLM）。

用法：uv run python scripts/verify_p1_subagents.py [--sections 5] [--target 8000]

同一写作任务跑两条路径（stub 检索源离线文献，模型真实生成）：
- 旧路径：限制工具集为拆分前的样子，主循环逐节 submit_section 直接撰写；
- 新路径：完整工具集，逐节 write_section 委托写作子代理，正文不进主对话。

对比口径：两条路径都在「纯写作 turn」内取主上下文增量（写作 turn 的
最后一次请求减第一次请求的 prompt tokens）——写作 turn 只开放逐节写作
工具，审校/评估/导出留给后续 turn，否则新路径会在同一 turn 里顺带完成
审校重写（重写 guidance 落在主对话），污染写作阶段的对比。拆分省的是
随正文量线性增长的部分；turn 起点的固定开销（system、工具 schema、
大纲与调研结论）不随写作增长，计入增量对比会稀释差距，故不计。论文
规模越大差距越明显。并校验新路径的结构性保证：写作 turn 主对话不出
现整节正文（修订 turn 允许提示词许可的小幅直改，单次超 300 字仍视为
泄漏）、各节由子代理写入并留有摘要、审校返回意见列表。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from verify_p0_routing import _StubProvider

from drpaper.agent.core import PaperAgent
from drpaper.agent.schemas import TOOL_SCHEMAS
from drpaper.app.config import load_config
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import ChatCallbacks, LLMClient

# 旧路径 = 拆分前的工具面：没有子代理派发工具
_OLD_TOOLS = {
    "search_literature",
    "set_target_chars",
    "begin_draft",
    "confirm_outline",
    "submit_section",
    "finish_draft",
}

# 写作 turn 的工具面：只开放逐节写作 + 收尾，审校/评估/导出留给后续 turn
_WRITE_TOOLS = {"write_section", "finish_draft"}

_START = time.monotonic()


def _log(message: str) -> None:
    """带时间戳的进度日志，实时落盘便于长任务观察。"""
    print(f"[{time.monotonic() - _START:6.0f}s] {message}", flush=True)


def _collect_usage(tag: str) -> tuple[ChatCallbacks, dict[str, int]]:
    counter = {
        "prompt": 0,
        "completion": 0,
        "rounds": 0,
        "first_prompt": 0,
        "last_prompt": 0,
    }

    def on_usage(prompt: int, completion: int, _total: int) -> None:
        counter["prompt"] += prompt
        counter["completion"] += completion
        counter["rounds"] += 1
        if not counter["first_prompt"]:
            counter["first_prompt"] = prompt  # 首个请求 ≈ turn 起点主上下文
        counter["last_prompt"] = prompt  # 最后一次请求的 prompt ≈ 当前主上下文

    def on_tool_end(name: str, seconds: float) -> None:
        _log(f"{tag} 工具 {name} 完成（{seconds:.1f}s）")

    return ChatCallbacks(on_usage=on_usage, on_tool_end=on_tool_end), counter


def _drive(
    agent: PaperAgent,
    text: str,
    schemas: list[dict],
    handlers: dict,
    usage: dict[str, int],
    max_rounds: int,
    tag: str,
) -> str:
    """追加用户输入并跑一轮 agent loop，token 计入 usage（子代理已单独统计）。"""
    _log(f"{tag} 用户输入: {text}")
    cb, counter = _collect_usage(tag)
    agent._toolbox.callbacks = cb  # 子代理的工具事件也透传到进度日志
    agent.messages.append({"role": "user", "content": text})
    reply = agent.llm.chat(
        agent.messages, schemas, handlers, callbacks=cb, max_rounds=max_rounds
    )
    if reply:
        agent.messages.append({"role": "assistant", "content": reply})
    usage["prompt"] += counter["prompt"]
    usage["completion"] += counter["completion"]
    usage["rounds"] = usage.get("rounds", 0) + counter["rounds"]
    usage["last_prompt"] = counter["last_prompt"] or usage.get("last_prompt", 0)
    _log(
        f"{tag} 本轮结束: 主循环 tokens {counter['prompt']:,} in / {counter['completion']:,} out"
        f"（{counter['rounds']} 轮，主上下文 {counter['first_prompt']:,}"
        f" → {counter['last_prompt']:,} tokens）"
    )
    return counter


def _dump_messages(agent: PaperAgent, name: str) -> None:
    """主对话历史与草稿落盘，供事后核对上下文构成与正文出处。"""
    path = f".output/verify_p1_subagents_{name}.json"
    dump = {
        "messages": agent.messages,
        "draft": [{"name": s.name, "content": s.content} for s in agent.draft.sections],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    _log(f"{name} 主对话历史已存 {path}")


def _sections_written(agent: PaperAgent) -> list[str]:
    return [s.name for s in agent.draft.sections if s.content]


def _main_text(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False)


def _submitted_content(tool_call: dict) -> str:
    """提取 submit_section 调用参数里的正文（arguments 可能是 JSON 串或已是 dict）。"""
    args = tool_call.get("function", {}).get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return ""
    return str(args.get("content", ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sections", type=int, default=5)
    parser.add_argument("--target", type=int, default=8000, help="全文目标字数")
    args = parser.parse_args()
    n_sections = max(3, min(args.sections, 6))
    target = max(3000, args.target)

    config = load_config()
    llm = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
    )
    topic = "大语言模型幻觉缓解"
    results: list[tuple[bool, str, str]] = []

    # ---- 旧路径：主循环逐节直接撰写（拆分前等价）----
    old_agent = PaperAgent(llm=llm, output_dir=".output", style=load_profile())
    old_agent._toolbox.provider = _StubProvider()
    old_agent.messages[0]["content"] += (
        f"\n\n（对照实验：research_topic / write_section / review_draft 工具本次不可用；"
        f"调研用 search_literature，逐节写作直接用 submit_section，大纲固定 {n_sections} 节。）"
    )
    old_schemas = [t for t in TOOL_SCHEMAS if t["function"]["name"] in _OLD_TOOLS]
    old_handlers = {
        k: v for k, v in old_agent._toolbox.handlers().items() if k in _OLD_TOOLS
    }
    old_usage: dict[str, int] = {"prompt": 0, "completion": 0}
    _drive(
        old_agent,
        f"帮我写一篇关于{topic}的论文，正文 {target} 字左右，共 {n_sections} 节。",
        old_schemas,
        old_handlers,
        old_usage,
        max_rounds=30,
        tag="[旧路径]",
    )
    old_ctr = _drive(
        old_agent,
        "可以，开始写，先只管逐节写完正文",
        old_schemas,
        old_handlers,
        old_usage,
        max_rounds=60,
        tag="[旧路径]",
    )
    # 写作 turn 内的主上下文增量：拆分省的正是随正文线性增长的部分
    old_delta = old_ctr["last_prompt"] - old_ctr["first_prompt"]
    old_written = _sections_written(old_agent)
    old_done = bool(old_written) and all(s.content for s in old_agent.draft.sections)
    results.append(
        (
            old_done,
            "旧路径：主循环逐节 submit_section 写完全部节",
            f"大纲 {len(old_agent.draft.sections)} 节，已完成: {old_written or ['无']}；"
            f"主循环 tokens {old_usage['prompt']:,} in / {old_usage['completion']:,} out",
        )
    )
    _dump_messages(old_agent, "old")

    # ---- 新路径：逐节 write_section 委托写作子代理 ----
    new_agent = PaperAgent(llm=llm, output_dir=".output", style=load_profile())
    new_agent._toolbox.provider = _StubProvider()
    new_usage: dict[str, int] = {"prompt": 0, "completion": 0}
    _drive(
        new_agent,
        f"帮我写一篇关于{topic}的论文，正文 {target} 字左右，共 {n_sections} 节。",
        TOOL_SCHEMAS,
        new_agent._toolbox.handlers(),
        new_usage,
        max_rounds=30,
        tag="[新路径]",
    )
    # 写作 turn 与旧路径工具面对称：只开放逐节写作 + 收尾，审校/评估/导出留给 turn 3
    write_schemas = [t for t in TOOL_SCHEMAS if t["function"]["name"] in _WRITE_TOOLS]
    write_handlers = {
        k: v for k, v in new_agent._toolbox.handlers().items() if k in _WRITE_TOOLS
    }
    new_ctr = _drive(
        new_agent,
        "可以，开始写，先只管逐节写完正文",
        write_schemas,
        write_handlers,
        new_usage,
        max_rounds=60,
        tag="[新路径]",
    )
    new_delta = new_ctr["last_prompt"] - new_ctr["first_prompt"]
    writing_end = len(new_agent.messages)  # 写作 turn 结束位置，泄漏检查以此为界
    _drive(
        new_agent,
        "写完了，请审校一遍，按意见修改后校验全文并导出",
        TOOL_SCHEMAS,
        new_agent._toolbox.handlers(),
        new_usage,
        max_rounds=60,
        tag="[新路径]",
    )
    sub_usage = new_agent._toolbox.subagent_usage
    new_written = _sections_written(new_agent)
    new_done = bool(new_written) and all(s.content for s in new_agent.draft.sections)

    # 主对话不出现整节正文（子代理路径的结构性保证）。
    # 写作 turn 全程严格禁止；修订 turn 允许提示词明确许可的小幅直改
    # （"小改直接 submit_section"），但单次直改超过 300 字视为整节重写、计为泄漏。
    writing_text = _main_text(new_agent.messages[:writing_end])
    leaked = [
        s.name
        for s in new_agent.draft.sections
        if len(s.content) > 50 and s.content in writing_text
    ]
    for m in new_agent.messages[writing_end:]:
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "submit_section":
                content = _submitted_content(tc)
                if len(content) > 300:
                    leaked.append(f"修订期直改 {len(content)} 字")
    results.append(
        (
            new_done and not leaked,
            "新路径：全部节均由写作子代理写入，整节正文不进主对话",
            f"大纲 {len(new_agent.draft.sections)} 节，已完成: {new_written or ['无']}"
            + (f"；泄漏进主对话的节: {leaked}" if leaked else "")
            + f"；子代理 tokens {sub_usage['prompt']:,} in / {sub_usage['completion']:,} out",
        )
    )

    # 各节摘要（主循环持有的"各章摘要"）
    digests = new_agent._toolbox.section_digests
    results.append(
        (
            all(digests.get(s.name) for s in new_agent.draft.sections),
            "主循环持有各节内容摘要（digest）",
            f"摘要数: {len(digests)}；示例: {next(iter(digests.values()), '无')[:40]}",
        )
    )

    # 写作阶段主上下文对比：两条路径都在「纯写作 turn」内取增量，
    # 排除 turn 起点固定开销与审校/评估等后续对话的干扰——
    # 拆分省的是随正文量线性增长的部分
    drop = 1 - new_delta / old_delta if old_delta > 0 else 0
    results.append(
        (
            old_delta > 0 and new_delta < old_delta * 0.7,
            "写作期间主上下文增长明显下降（新路径增量 < 旧路径 70%）",
            f"写作 turn 主上下文增量：旧 +{old_delta:,} → 新 +{new_delta:,} tokens"
            f"（下降 {drop:.0%}）；写作结束时刻主上下文：旧 {old_usage['last_prompt']:,}"
            f" / 新 {new_usage['last_prompt']:,}；"
            f"累计请求 tokens：旧 {old_usage['prompt']:,}（{old_usage.get('rounds', 0)} 轮）"
            f" / 新 {new_usage['prompt']:,}（{new_usage.get('rounds', 0)} 轮，含审校重写）；"
            f"子代理另计 {sub_usage['prompt']:,}",
        )
    )

    # 审校子代理返回结构化意见列表
    review_results = [
        json.loads(m["content"])
        for m in new_agent.messages
        if m.get("role") == "tool" and '"issues"' in m.get("content", "")
    ]
    results.append(
        (
            bool(review_results) and review_results[-1].get("status") == "ok",
            "审校子代理返回分级意见列表",
            f"意见数: {len(review_results[-1].get('issues', []))}；"
            f"summary: {review_results[-1].get('summary', '无')[:60]}",
        )
    )

    _dump_messages(new_agent, "new")

    passed = 0
    for ok, label, detail in results:
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        print(f"      {detail}\n")
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
