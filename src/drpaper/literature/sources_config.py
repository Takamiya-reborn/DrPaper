"""检索源配置：内置 + 用户两级 sources.yaml 加载（与样式档案同款模式）。

- 内置 sources.yaml 随包分发（打包时由 package.spec 带入）
- 用户文件：打包运行取 exe 同级 sources.yaml，源码运行取 ~/.drpaper/sources.yaml
- 用户文件浅合并覆盖内置：只覆盖出现的源与字段，其余沿用内置
- API key/mailto 支持环境变量覆盖（SEMANTIC_SCHOLAR_API_KEY / OPENALEX_MAILTO），
  .env 在 app.config.load_config 时已加载
- openalex 段的 mailto 与 api_key 两个键等价，均可写
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from drpaper.runtime import user_resource_path

BUILTIN_SOURCES_PATH = Path(__file__).parent / "sources.yaml"

# 环境变量名 → 源配置字段：环境变量优先于 YAML
_ENV_OVERRIDES = {
    "semantic_scholar": {"api_key": "SEMANTIC_SCHOLAR_API_KEY"},
    "openalex": {"api_key": "OPENALEX_MAILTO"},  # openalex 的 api_key 字段即 mailto
}


@dataclass(frozen=True)
class SourceConfig:
    """单个检索源的开关、分层与预算参数。"""

    name: str
    enabled: bool
    tier: int
    weight: float
    max_results: int
    per_session_calls: int
    api_key: str = ""


@dataclass(frozen=True)
class SourcesConfig:
    """全部检索源配置：按 tier 升序、tier 内 weight 降序排列。"""

    sources: dict[str, SourceConfig]
    max_total: int
    min_authority_score: float

    def ordered(self) -> list[SourceConfig]:
        """启用且已注册的源按检索优先级排序返回。"""
        enabled = [s for s in self.sources.values() if s.enabled]
        return sorted(enabled, key=lambda s: (s.tier, -s.weight))


def user_sources_path() -> Path:
    """用户配置路径：打包运行取 exe 同级 sources.yaml，源码运行取 ~/.drpaper/sources.yaml。"""
    return user_resource_path("sources.yaml")


def load_sources_config() -> SourcesConfig:
    """内置配置为底，用户文件浅合并覆盖同名字段；解析/校验失败抛带指引的 SystemExit。"""
    base = _read_yaml(BUILTIN_SOURCES_PATH)
    user_path = user_sources_path()
    user = _read_yaml(user_path) if user_path.is_file() else {}
    merged = {**base, **user}

    known = set(base.get("sources", {}))
    unknown = set(merged.get("sources", {})) - known
    if unknown:
        raise SystemExit(
            f"sources.yaml 出现未知检索源 {sorted(unknown)}，可用：{sorted(known)}"
        )
    return _parse(merged, user_path if user else BUILTIN_SOURCES_PATH)


def _read_yaml(path: Path) -> dict:
    """读取 YAML 文件为 dict；缺失返回空 dict，解析失败抛 SystemExit。"""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"检索源配置 YAML 解析失败：{path}\n{exc}") from None
    return data or {}


def _parse(data: dict, path: Path) -> SourcesConfig:
    """校验并组装 SourcesConfig，字段不合法时抛带指引的 SystemExit。"""
    try:
        policy = data["policy"]
        max_total = int(policy["max_total"])
        min_score = float(policy.get("min_authority_score", 0.0))
        raw_sources = data["sources"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"检索源配置缺少或类型错误的字段：{path}\n{exc}") from None
    if max_total < 1 or min_score < 0:
        raise SystemExit(f"检索源配置 policy 数值不合法（max_total ≥ 1，min_authority_score ≥ 0）：{path}")

    sources: dict[str, SourceConfig] = {}
    for name, raw in raw_sources.items():
        try:
            config = SourceConfig(
                name=name,
                enabled=bool(raw["enabled"]),
                tier=int(raw["tier"]),
                weight=float(raw["weight"]),
                max_results=int(raw["max_results"]),
                per_session_calls=int(raw["per_session_calls"]),
                # openalex 段 mailto 与 api_key 两键等价，兼容两种写法
                api_key=str(raw.get("api_key") or raw.get("mailto", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"检索源「{name}」缺少或类型错误的字段：{path}\n{exc}") from None
        if config.tier < 1 or config.weight < 0 or config.max_results < 1 or config.per_session_calls < 0:
            raise SystemExit(
                f"检索源「{name}」数值不合法（tier ≥ 1，weight ≥ 0，max_results ≥ 1，per_session_calls ≥ 0）：{path}"
            )
        sources[name] = _apply_env(name, config)
    return SourcesConfig(sources=sources, max_total=max_total, min_authority_score=min_score)


def _apply_env(name: str, config: SourceConfig) -> SourceConfig:
    """环境变量优先：覆盖源配置中的 key/mailto 字段。"""
    mapping = _ENV_OVERRIDES.get(name, {})
    for field_name, env_name in mapping.items():
        value = os.getenv(env_name, "").strip()
        if value:
            config = SourceConfig(**{**config.__dict__, "api_key": value})
    return config
