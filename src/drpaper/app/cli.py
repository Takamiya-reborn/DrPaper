"""CLI 交互入口：REPL 对话循环 + 状态行渲染 + token 统计。"""

from __future__ import annotations

import argparse
import itertools
import os
import random
import sys
import threading
import time
from collections.abc import Callable

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.app.phrases import WORKING_PHRASES
from drpaper.export.style_profile import load_profile
from drpaper.llm.client import ChatCallbacks, LLMClient

WELCOME = """\
=== DrPaper —— 中文学术写作 Agent ===

- 论文起草：告诉我研究方向、课题和字数要求，我先在多个学术数据源
  （arXiv / OpenAlex / Semantic Scholar）检索真实文献，
  再为你生成一份结构规范、格式合规的论文初稿（Word 格式）。
- 论文修改：把论文或段落交给我，我逐项检查结构、论证与表达，
  按严重程度分级定位问题，逐条给出修改建议；也支持学术润色、课题查新与实验设计。
- 研究指导：问我"某领域还有什么方向可做"，或让我推荐几篇
  能接着往下做的论文，并给出具体接续点；问"这个课题有没有人做过"会走课题查新。
- 多轮修改与提问：生成后可继续要求修改，或提问"摘要怎么写"之类的方法问题。

示例输入：研究方向是大语言模型，课题是"检索增强生成缓解大模型幻觉"，正文 5000 字左右。
指导示例：大语言模型在教育领域还有什么方向可做？推荐几篇能接着做的论文。

输入 exit 或按 Ctrl+C 退出。
"""


def _parse_args() -> argparse.Namespace:
    """解析启动参数：--library 外接本地文献库（可重复，文件或目录）。"""
    parser = argparse.ArgumentParser(
        prog="drpaper", description="中文学术写作 Agent：多源检索真实文献，生成中文论文初稿"
    )
    parser.add_argument(
        "--library",
        action="append",
        default=[],
        metavar="PATH",
        help="外接本地文献库：.bib/.ris/.jsonl 文件或目录，可重复传入；"
        "用户文献在检索结果中优先返回并优先引用（也可在 sources.yaml 的 paths 配置）",
    )
    return parser.parse_args()


class StatusLine:
    """终端状态行：旋转符 + 文案，\\r 覆写，与流式正文输出互斥。"""

    _FRAMES = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    _FRAME_INTERVAL = 0.1
    _PHRASE_INTERVAL = 2.0

    def __init__(self, phrases: list[str], counter: Callable[[], int]) -> None:
        self._phrases = phrases
        self._counter = counter
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def show(self) -> None:
        """显示旋转状态行，文案每 2 秒随机轮换（已有线程在跑则继续）。"""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._spin, daemon=True)
                self._thread.start()

    def clear(self) -> None:
        """停止旋转并清整行。"""
        self._halt()
        with self._lock:
            _write(sys.stdout, "\r\x1b[K")

    def _halt(self) -> None:
        with self._lock:
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(0.3)

    def _spin(self) -> None:
        phrase = random.choice(self._phrases)
        next_swap = time.monotonic() + self._PHRASE_INTERVAL
        while not self._stop.wait(self._FRAME_INTERVAL):
            if time.monotonic() >= next_swap:
                phrase = random.choice(self._phrases)
                next_swap = time.monotonic() + self._PHRASE_INTERVAL
            tokens = self._counter()
            suffix = f"（已耗 {tokens:,} tokens）" if tokens else ""
            with self._lock:
                _write(sys.stdout, f"\r{next(self._FRAMES)} {phrase}{suffix}\x1b[K")


def _write(stream: object, text: str) -> None:
    """写入一行状态文本，编码不支持的字符降级替换。"""
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        enc = stream.encoding or "utf-8"
        stream.write(text.encode(enc, "replace").decode(enc))
        stream.flush()


def _use_utf8_stdio() -> None:
    """标准流强制 UTF-8，避免 Windows 管道/重定向下的编码错乱。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """启动交互式会话。"""
    _use_utf8_stdio()
    os.system("")  # 启用 Windows 终端的 ANSI 转义
    args = _parse_args()
    config = load_config()
    profile = load_profile()
    agent = PaperAgent(
        llm=LLMClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        ),
        output_dir=config.output_dir,
        style=profile,
        library_paths=args.library,
    )
    for warning in agent.library_warnings:
        print(warning, file=sys.stderr)
    # 会话累计 token：输入 / 输出
    usage_total = {"prompt": 0, "completion": 0}
    # 状态行实时计数器：已结算轮次 + 当前轮实时用量
    status = StatusLine(
        WORKING_PHRASES,
        counter=lambda: (
            usage_total["prompt"]
            + round_usage["prompt"]
            + usage_total["completion"]
            + round_usage["completion"]
        ),
    )

    def on_tool_start(_name: str, _args: dict) -> None:
        status.show()

    print(WELCOME)
    print(f"当前排版规范：{profile.name}\n")
    turn = 0
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

        turn += 1
        round_usage = {"prompt": 0, "completion": 0}

        def on_usage(
            prompt: int, completion: int, _total: int, _u: dict = round_usage
        ) -> None:
            _u["prompt"] += prompt
            _u["completion"] += completion

        callbacks = ChatCallbacks(
            on_text=_print_stream,
            on_first_token=status.clear,
            on_round_start=status.show,
            on_tool_start=on_tool_start,
            on_usage=on_usage,
        )

        print("\nDrPaper> ", end="", flush=True)
        try:
            agent.chat(user_input, callbacks=callbacks)
            if turn == 1 and not agent.last_target_detected:
                print(
                    "（未检测到目标字数，默认按 5000 字撰写，可随时说「改成 8000 字」调整。）"
                )
        except KeyboardInterrupt:
            print("\n[已中断本轮，可继续输入]", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 单轮异常不终止会话
            print(f"\n[本轮出错] {exc}", file=sys.stderr)
        finally:
            status.clear()

        usage_total["prompt"] += round_usage["prompt"]
        usage_total["completion"] += round_usage["completion"]
        if round_usage["prompt"] or round_usage["completion"]:
            total = usage_total["prompt"] + usage_total["completion"]
            print(
                f"\033[2mtokens 输入 {round_usage['prompt']:,} / 输出 {round_usage['completion']:,}"
                f" ｜ 累计 {total:,}\033[0m"
            )


def _print_stream(text: str) -> None:
    """流式打印模型正文输出。"""
    print(text, end="", flush=True)
