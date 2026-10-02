"""CLI 交互入口：REPL 对话循环。"""

from __future__ import annotations

import sys

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.llm.client import LLMClient

WELCOME = """\
=== DrPaper —— 中文学术写作 Agent ===

- 论文起草：告诉我研究方向、课题和字数要求，我先在 arXiv 检索真实文献，
  再为你生成一份结构规范、格式合规的论文初稿（Word 格式）。
- 论文修改：把论文或段落交给我，我逐项检查结构、论证与表达，
  按严重程度分级定位问题，逐条给出修改建议；也支持学术润色、课题查新与实验设计。
- 多轮修改与提问：生成后可继续要求修改，或提问"摘要怎么写"之类的方法问题。

示例输入：研究方向是大语言模型，课题是"检索增强生成缓解大模型幻觉"，正文 5000 字左右。

输入 exit 或按 Ctrl+C 退出。
"""


def _use_utf8_stdio() -> None:
    """标准流强制 UTF-8，避免 Windows 管道/重定向下的编码错乱。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """启动交互式会话。"""
    _use_utf8_stdio()
    config = load_config()
    agent = PaperAgent(
        llm=LLMClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        ),
        output_dir=config.output_dir,
    )

    print(WELCOME)
    while True:
        try:
            user_input = input("\n你> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见，祝写作顺利！")
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("再见，祝写作顺利！")
            break

        print("\nDrPaper> ", end="", flush=True)
        try:
            agent.chat(user_input, on_text=_print_stream)
        except KeyboardInterrupt:
            print("\n[已中断本轮，可继续输入]", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 单轮异常不终止会话
            print(f"\n[本轮出错] {exc}", file=sys.stderr)


def _print_stream(text: str) -> None:
    """流式打印模型正文输出。"""
    print(text, end="", flush=True)
