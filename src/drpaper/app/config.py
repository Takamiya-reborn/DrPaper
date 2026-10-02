"""应用配置：读取 .env 与环境变量。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class AppConfig:
    """运行所需配置。"""

    api_key: str
    base_url: str
    model: str
    output_dir: str


def load_config() -> AppConfig:
    """加载配置；缺少必要项时抛出带指引的错误。"""
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")
    model = os.getenv("CURRENT_MODEL", DEFAULT_MODEL)
    output_dir = os.getenv("DRPAPER_OUTPUT_DIR", ".output")

    if not api_key:
        raise SystemExit(
            "未检测到 OPENAI_API_KEY，请在项目根目录创建 .env 文件（可参考 .env.example）"
        )

    return AppConfig(
        api_key=api_key, base_url=base_url, model=model, output_dir=output_dir
    )
