"""Semantic Scholar 检索源实现。"""

from __future__ import annotations

from drpaper.literature.base import Paper, clean_authors, normalize_ws
from drpaper.literature.sources.http import get_json

# Semantic Scholar Graph API 论文检索端点
_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# 请求的字段：元数据 + 权威度信号（引用数、发表载体）
_FIELDS = "title,authors,year,abstract,externalIds,citationCount,venue,url"


class SemanticScholarProvider:
    """基于 Semantic Scholar Graph API 的检索实现（免费档限流，宜作低层源）。"""

    name = "semantic_scholar"

    def __init__(self, api_key: str = "") -> None:
        # 有 API key 时请求头带上，可提升速率上限；留空走免费档
        self._api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[Paper]:
        """按关键词检索 Semantic Scholar，按相关性排序。"""
        headers = {"x-api-key": self._api_key} if self._api_key else None
        data = get_json(
            _API_URL,
            params={"query": query, "limit": str(max_results), "fields": _FIELDS},
            headers=headers,
        )
        return [_to_paper(item) for item in data.get("data", [])]


def _to_paper(item: dict) -> Paper:
    """把 Semantic Scholar 论文记录映射为 Paper。"""
    external_ids = item.get("externalIds") or {}
    doi = (external_ids.get("DOI") or "").lower()
    return Paper(
        uid=doi or item.get("paperId", ""),
        title=normalize_ws(item.get("title") or ""),
        authors=clean_authors(a.get("name", "") for a in item.get("authors", [])),
        year=item.get("year") or 0,
        abstract=item.get("abstract") or "",
        url=item.get("url") or "",
        tags=[],
        citations=item.get("citationCount") or 0,
        venue=item.get("venue") or "",
    )
