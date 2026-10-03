"""CLI 交互入口：REPL 对话循环 + 状态行渲染 + token 统计。"""

from __future__ import annotations

import itertools
import os
import random
import sys
import threading

from drpaper.agent.core import PaperAgent
from drpaper.app.config import load_config
from drpaper.llm.client import ChatCallbacks, LLMClient

WELCOME = """\
=== DrPaper —— 中文学术写作 Agent ===

- 论文起草：告诉我研究方向、课题和字数要求，我先在 arXiv 检索真实文献，
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

# 工具执行的固定趣味短语（CLI 端渲染，零 API token）：(进行中, 完成时)
_TOOL_PHRASES: dict[str, list[tuple[str, str]]] = {
    "search_arxiv": [("正在挖钻石 ⛏", "挖到了钻石"), ("正在下矿探险", "满载而归")],
    "load_skill": [("正在翻阅卷轴 📜", "卷轴已读完")],
    "begin_draft": [("正在绘制地图 🗺", "地图绘制完成")],
    "submit_section": [
        ("正在生成叶绿 🌿", "叶绿生成完毕"),
        ("正在浇筑混凝土", "浇筑成型"),
        ("正在铺设轨道", "轨道铺设完毕"),
    ],
    "finish_draft": [("正在合成工作台", "工作台已合成")],
    "export_docx": [("正在点燃熔炉 🔥", "出炉了")],
    "remove_papers": [("正在清理背包", "背包整理完毕")],
}

# 模型思考期间（请求已发出、还没有任何输出）的固定短语
_THINK_PHRASES = ["思考中", "脑内风暴中", "翻阅记忆中"]


class StatusLine:
    """终端状态行：旋转符 + 文案，\\r 覆写，与流式正文输出互斥。"""

    _FRAMES = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    _INTERVAL = 0.1

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._text = ""

    def show(self, text: str) -> None:
        """显示/更新旋转状态行（已有线程在跑则只换文案）。"""
        with self._lock:
            self._text = text
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._spin, daemon=True)
                self._thread.start()

    def clear(self) -> None:
        """停止旋转并清整行。"""
        self._halt()
        with self._lock:
            _write(sys.stdout, "\r\x1b[K")

    def finish(self, text: str) -> None:
        """停止旋转，原地覆写为完成文案并换行（保留进度痕迹）。"""
        self._halt()
        with self._lock:
            _write(sys.stdout, f"\r\x1b[K✓ {text}\n")

    def _halt(self) -> None:
        with self._lock:
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(0.3)

    def _spin(self) -> None:
        while not self._stop.wait(self._INTERVAL):
            with self._lock:
                _write(sys.stdout, f"\r{next(self._FRAMES)} {self._text}\x1b[K")


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
    config = load_config()
    agent = PaperAgent(
        llm=LLMClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        ),
        output_dir=config.output_dir,
    )
    status = StatusLine()
    # 会话累计 token：输入 / 输出
    usage_total = {"prompt": 0, "completion": 0}
    # 当前工具进行中的短语（完成时配对使用）
    active_phrase: list[str] = []

    def on_tool_start(name: str, _args: dict) -> None:
        phrases = _TOOL_PHRASES.get(name)
        if phrases:
            active_phrase.clear()
            active_phrase.append(random.choice(phrases)[0])
            status.show(active_phrase[0])

    def on_tool_end(name: str, elapsed: float) -> None:
        phrases = _TOOL_PHRASES.get(name)
        if not phrases:
            return
        # 优先复用开始时选中的短语去掉"正在"作完成文案
        text = active_phrase[0].removeprefix("正在") if active_phrase else phrases[0][1]
        status.finish(f"{text}完毕 ({elapsed:.1f}s)")

    print(WELCOME)
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

        def on_usage(prompt: int, completion: int, _total: int,
                     _u: dict = round_usage) -> None:
            _u["prompt"] += prompt
            _u["completion"] += completion

        callbacks = ChatCallbacks(
            on_text=_print_stream,
            on_first_token=status.clear,
            on_round_start=lambda: status.show(random.choice(_THINK_PHRASES)),
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_usage=on_usage,
        )

        print("\nDrPaper> ", end="", flush=True)
        try:
            agent.chat(user_input, callbacks=callbacks)
            if turn == 1 and not agent.last_target_detected:
                print("（未检测到目标字数，默认按 5000 字撰写，可随时说「改成 8000 字」调整。）")
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
