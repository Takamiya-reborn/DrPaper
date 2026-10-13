"""应用配置：读取 .env 与环境变量。

用户面只有 KEY 与 URL 两个概念：用哪个 API Key，就采用该 key 对应的
接口协议与地址；多个 key 并存时由启动菜单选择（见 app.selector 与 cli）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS = ("openai", "anthropic")

# .env 固定取运行目录（打包运行即 exe 同级目录），显式加载，避免 dotenv
# 按源码位置向上搜索摸到别处的配置；报错提示也统一给出该绝对路径
ENV_PATH = Path(".env").resolve()

_ENV_TEMPLATE = """\
# DrPaper 配置文件（由程序自动生成，填写后重新启动）
# 必填：模型名（如 gpt-4o-mini、claude-sonnet-4-5，或所用服务商支持的名称）
CURRENT_MODEL=
# 必填二选一：API 密钥
OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# 可选：接口地址（官方接口留空；第三方兼容服务填完整根地址）
# OPENAI_BASE_URL=
# ANTHROPIC_BASE_URL=
"""


@dataclass(frozen=True)
class AppConfig:
    """运行所需配置。"""

    provider: str  # 协议标识，由选中的 API Key 决定：openai（含兼容接口）| anthropic
    api_key: str
    base_url: str
    model: str
    output_dir: str


def load_config(provider: str | None = None) -> AppConfig:
    """加载配置；provider 缺省时按已填写的 API Key 识别，无法唯一确定时报错。"""
    _load_env()

    provider = provider or _sole_provider()
    key_var = f"{provider.upper()}_API_KEY"
    api_key = os.getenv(key_var, "")
    if not api_key:
        raise SystemExit(
            f"未检测到 {key_var}：请检查 .env 文件（{ENV_PATH}），"
            "填写后重新启动"
        )

    model = os.getenv("CURRENT_MODEL", "")
    if not model:
        raise SystemExit(
            f"未检测到 CURRENT_MODEL：请检查 .env 文件（{ENV_PATH}），"
            "填写模型名（如 gpt-4o-mini、claude-sonnet-4-5，或所用服务商支持的名称）"
            "后重新启动"
        )

    base_url = os.getenv(f"{provider.upper()}_BASE_URL", "")
    output_dir = os.getenv("DRPAPER_OUTPUT_DIR", ".output")

    return AppConfig(
        provider=provider, api_key=api_key, base_url=base_url, model=model, output_dir=output_dir
    )


def configured_providers() -> list[str]:
    """按固定顺序返回已填写 API Key 的 provider 列表。"""
    return [name for name in PROVIDERS if os.getenv(f"{name.upper()}_API_KEY", "").strip()]


def detect_provider() -> str | None:
    """按已填写的 API Key 识别 provider；零个或多个时返回 None，交由上层处理。"""
    _load_env()
    configured = configured_providers()
    return configured[0] if len(configured) == 1 else None


def _load_env() -> None:
    """加载运行目录的 .env；真实环境变量优先于文件内容。"""
    load_dotenv(dotenv_path=ENV_PATH, override=False)


def _sole_provider() -> str:
    """无法唯一确定 provider 时的兜底报错（正常路径由启动菜单先行选择）。"""
    configured = configured_providers()
    if len(configured) == 1:
        return configured[0]
    if not configured:
        if _ensure_env_template():
            raise SystemExit(
                f"未检测到 API 密钥：已在 {ENV_PATH} 生成 .env 配置模板，"
                "请填写 OPENAI_API_KEY（或 ANTHROPIC_API_KEY）与 CURRENT_MODEL 后重新启动"
            )
        raise SystemExit(
            f"未检测到 API 密钥：请检查 .env 文件（{ENV_PATH}），"
            "填写 OPENAI_API_KEY（或 ANTHROPIC_API_KEY）与 CURRENT_MODEL 后重新启动"
        )
    keys = " 与 ".join(f"{name.upper()}_API_KEY" for name in configured)
    raise SystemExit(f"检测到多个 API 密钥（{keys}），请仅保留一个或通过启动菜单选择")


def _ensure_env_template() -> bool:
    """运行目录没有 .env 时生成配置模板；已存在则不动，返回是否新生成。"""
    if ENV_PATH.exists():
        return False
    ENV_PATH.write_text(_ENV_TEMPLATE, encoding="utf-8")
    return True
