"""终端选择菜单：BIOS 风格的 ↑↓ 移动 + Enter 确认。

仅依赖标准库：Windows 用 msvcrt 读取按键并在原位重绘（ANSI 转义由
cli 启动时启用）；其他平台退化为输入序号选择。
"""

from __future__ import annotations

import sys


def select_option(title: str, options: list[str]) -> int:
    """显示选择菜单，返回选中项下标；取消（Ctrl+C / Esc）时抛 KeyboardInterrupt。"""
    if sys.platform != "win32":
        return _select_by_number(title, options)

    print(title, flush=True)
    selected = 0
    drawn = False

    def draw() -> None:
        """重绘选项区：非首次绘制前先把光标移回选项首行。"""
        nonlocal drawn
        if drawn:
            sys.stdout.write(f"\033[{len(options)}A")
        for i, option in enumerate(options):
            cursor = "> " if i == selected else "  "
            style = "\033[1m" if i == selected else "\033[0m"
            sys.stdout.write(f"\033[2K{style}{cursor}{option}\033[0m\r\n")
        sys.stdout.flush()
        drawn = True

    while True:
        draw()
        key = _read_key()
        if key == "up":
            selected = (selected - 1) % len(options)
        elif key == "down":
            selected = (selected + 1) % len(options)
        elif key == "enter":
            print()
            return selected
        elif key == "quit":
            raise KeyboardInterrupt


def _read_key() -> str:
    """读取一个按键，返回 up / down / enter / quit；其余按键忽略。"""
    import msvcrt

    key = msvcrt.getch()
    if key in (b"\x00", b"\xe0"):  # 功能键前缀，第二个字节才是键值
        key = msvcrt.getch()
        return {"H": "up", "P": "down"}.get(key.decode("ascii", errors="ignore"), "")
    if key in (b"\r", b"\n", b" "):
        return "enter"
    if key == b"\x1b":
        return "quit"
    if key == b"\x03":  # Ctrl+C 在 getch 下不会自动抛出，需显式处理
        raise KeyboardInterrupt
    return ""


def _select_by_number(title: str, options: list[str]) -> int:
    """非 Windows 的退化实现：列出序号，输入数字选择。"""
    print(title)
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    while True:
        raw = input("输入序号选择: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
