"""文献聚合器：分层检索 + 会话预算 + 跨源去重 + 权威度排序。

按 sources.yaml 的 tier 分层检索：高层源结果足够（去重后达到目标条数）
就不再调用低层源，稀缺配额（如 Semantic Scholar 免费档）只在必要时消耗；
最终按权威分降序截断到 max_total，控制送入模型的文献量。

用户外接的本地文献库（local_library，tier 0）优先级最高，体现在三处：
最先检索；结果固定前置、不被 max_total 截断淘汰（priority 标记）；
与在线源同键去重时保留用户版元数据。

跨源去重：有 DOI 用归一化 DOI 作 uid（OpenAlex 与 Semantic Scholar 都提供），
否则用归一化标题兜底。已知局限：arXiv 预印本与其正式版仅当标题归一化一致时
才能去重，不一致时会各留一条，由文献库的 uid 去重兜底。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from drpaper.literature.authority import authority_score
from drpaper.literature.base import Paper
from drpaper.literature.sources_config import SourceConfig, load_sources_config

# 注册表：源名 → 提供者构造函数（接收源配置）；新增数据源在此登记
_PROVIDER_FACTORIES: dict[str, Callable[[SourceConfig], Any]] = {}


class _Provider(Protocol):
    """聚合器所需的检索源最小接口。"""

    name: str

    def search(self, query: str, max_results: int) -> list[Paper]: ...


@dataclass
class _Entry:
    """聚合内部条目：论文 + 来源名 + 权威分 + 用户文献标记。"""

    paper: Paper
    source: str
    score: float
    priority: bool = False  # 用户外接文献库的条目，排序前置且不被截断淘汰


class LiteratureAggregator:
    """实现 SearchProvider 协议的组合检索源；实例按会话持有配额账本。"""

    name = "literature"

    def __init__(
        self,
        providers: list[tuple[Any, SourceConfig]],
        max_total: int,
        min_authority_score: float,
        warnings: list[str] | None = None,
    ) -> None:
        # providers 已按 (tier, -weight) 排序
        self._providers = providers
        self._max_total = max_total
        self._min_authority_score = min_authority_score
        self.warnings = warnings or []  # 本地文献库加载警告，启动时向用户展示
        # 会话配额账本：源名 → 已调用次数
        self._calls: dict[str, int] = {}

    def search(self, query: str, max_results: int) -> list[Paper]:
        """分层检索：逐层调用源并去重合并，达标即止，用户文献前置后按权威分截断。"""
        target = max_results
        pool: dict[tuple[str, str], _Entry] = {}
        for group in self._tier_groups():
            self._search_group(query, group, target, pool)
            # 去重后已够目标条数，或权威分达到门槛，不再调用更低层
            if len(pool) >= target:
                break
            if self._min_authority_score > 0 and pool:
                best = max(entry.score for entry in pool.values())
                if best >= self._min_authority_score:
                    break
        ranked = sorted(pool.values(), key=lambda e: (not e.priority, -e.score))
        return [entry.paper for entry in ranked[: self._max_total]]

    def _tier_groups(self) -> list[list[tuple[Any, SourceConfig]]]:
        """把已排序的源按 tier 分组，保持组内 weight 降序。"""
        groups: list[list[tuple[Any, SourceConfig]]] = []
        for provider, config in self._providers:
            if not groups or groups[-1][0][1].tier != config.tier:
                groups.append([(provider, config)])
            else:
                groups[-1].append((provider, config))
        return groups

    def _search_group(
        self,
        query: str,
        group: list[tuple[Any, SourceConfig]],
        target: int,
        pool: dict[tuple[str, str], _Entry],
    ) -> None:
        """检索一层内的所有源（同层都是低成本源，应全部覆盖）：
        跳过预算用尽或故障的源，结果并入去重池；降级判断只在层与层之间做。"""
        for provider, config in group:
            if self._calls.get(config.name, 0) >= config.per_session_calls:
                continue  # 该源本会话预算用尽
            try:
                papers = provider.search(query, min(config.max_results, target))
            except Exception:
                continue  # 单源故障降级跳过，不影响整体检索
            self._calls[config.name] = self._calls.get(config.name, 0) + 1
            self._merge(pool, papers, config, priority=getattr(provider, "priority", False))

    def _merge(
        self,
        pool: dict[tuple[str, str], _Entry],
        papers: list[Paper],
        config: SourceConfig,
        priority: bool = False,
    ) -> None:
        """去重合并：键碰撞时用户文献胜出，其余保留高分条目并补齐缺失字段。"""
        for paper in papers:
            score = authority_score(paper, config.weight)
            existing = pool.get(_dedupe_key(paper))
            if existing is None:
                pool[_dedupe_key(paper)] = _Entry(
                    paper=paper, source=config.name, score=score, priority=priority
                )
                continue
            if priority != existing.priority:
                # 用户文献与在线源同键：保留用户版元数据（用户可能修正过标题、补过摘要）
                keep, other = (paper, existing.paper) if priority else (existing.paper, paper)
            elif score > existing.score:
                keep, other = paper, existing.paper
            else:
                keep, other = existing.paper, paper
            existing.paper = _fill(keep, other)
            existing.score = max(existing.score, score)
            existing.priority = existing.priority or priority


def _dedupe_key(paper: Paper) -> tuple[str, str]:
    """去重键：uid 为 DOI 用 ('doi', doi)，否则 ('title', 归一化标题)。"""
    if _is_doi(paper.uid):
        return ("doi", paper.uid.lower())
    return ("title", _norm_title(paper.title))


def _is_doi(uid: str) -> bool:
    """判断 uid 是否为 DOI 形式（10.xxxx/...）。"""
    return uid.lower().startswith("10.") and "/" in uid


def _norm_title(title: str) -> str:
    """标题归一化：小写、去非字母数字，供跨源去重兜底。"""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _fill(keep: Paper, extra: Paper) -> Paper:
    """字段补齐：以 keep 为主，uid 择优取 DOI（利于文献库跨调用去重）。"""
    uid = keep.uid if _is_doi(keep.uid) else (extra.uid if _is_doi(extra.uid) else keep.uid)
    return Paper(
        uid=uid,
        title=keep.title,
        authors=keep.authors,
        year=keep.year or extra.year,
        abstract=keep.abstract or extra.abstract,
        url=keep.url or extra.url,
        tags=keep.tags or extra.tags,
        citations=max(keep.citations, extra.citations),
        venue=keep.venue or extra.venue,
        origin=keep.origin if keep.origin else extra.origin,
    )


def default_aggregator(library_paths: Sequence[str] = ()) -> LiteratureAggregator:
    """加载 sources.yaml 与外接路径，实例化启用的源并组装聚合器（agent 唯一调用点）。"""
    _ensure_factories()
    config = load_sources_config()
    providers: list[tuple[Any, SourceConfig]] = []
    warnings: list[str] = []
    # 本地文献库显式构造：需要合并 CLI 外接路径，且加载警告要向上传递
    library = _build_library(config, library_paths)
    if library is not None:
        provider, library_config = library
        warnings.extend(provider.warnings)
        providers.append((provider, library_config))
    for source_config in config.ordered():
        if source_config.name == "local_library":
            continue  # 上面已显式构造
        factory = _PROVIDER_FACTORIES.get(source_config.name)
        if factory is not None:
            providers.append((factory(source_config), source_config))
    return LiteratureAggregator(
        providers=providers,
        max_total=config.max_total,
        min_authority_score=config.min_authority_score,
        warnings=warnings,
    )


def _build_library(
    config: SourcesConfig, library_paths: Sequence[str]
) -> tuple[Any, SourceConfig] | None:
    """构造本地文献库源：无配置且无外接路径时返回 None，聚合行为与旧版完全一致。

    CLI --library 的路径优先级高于配置开关：用户配置里关掉了 local_library
    但启动时显式传了路径，仍然挂载。旧版用户 sources.yaml 没有 local_library
    条目时用内置默认参数兜底，保证 --library 不依赖配置文件是否最新。
    """
    cli_paths = tuple(path for path in library_paths if path)
    library_config = config.sources.get("local_library")
    if library_config is None:
        if not cli_paths:
            return None
        library_config = SourceConfig(
            name="local_library", enabled=True, tier=0, weight=1.0,
            max_results=10, per_session_calls=99,
        )
    if not library_config.enabled and not cli_paths:
        return None
    from drpaper.literature.sources.local import LocalLibraryProvider

    provider = LocalLibraryProvider(paths=tuple(library_config.paths) + cli_paths)
    if not provider.papers:
        return None  # 空库不占检索层级，聚合行为退化为纯在线源
    return provider, library_config


def _ensure_factories() -> None:
    """登记各检索源的构造函数（延迟 import，保持模块加载轻量）。"""
    if _PROVIDER_FACTORIES:
        return
    from drpaper.literature.sources.arxiv import ArxivProvider
    from drpaper.literature.sources.openalex import OpenAlexProvider
    from drpaper.literature.sources.semantic_scholar import SemanticScholarProvider

    _PROVIDER_FACTORIES.update(
        {
            "arxiv": lambda config: ArxivProvider(),
            "openalex": lambda config: OpenAlexProvider(mailto=config.api_key),
            "semantic_scholar": lambda config: SemanticScholarProvider(
                api_key=config.api_key
            ),
        }
    )
