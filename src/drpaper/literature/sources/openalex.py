"""OpenAlex 检索源实现。"""

from __future__ import annotations

from drpaper.literature.base import Paper, clean_authors, normalize_ws
from drpaper.literature.sources.http import get_json

# OpenAlex works 检索端点
_API_URL = "https://api.openalex.org/works"

# DOI 前缀，统一去掉后作为跨源 uid
_DOI_PREFIX = "https://doi.org/"


class OpenAlexProvider:
    """基于 OpenAlex 开放学术图谱的检索实现（免费、全学科、带引用数）。"""

    name = "openalex"

    def __init__(self, mailto: str = "") -> None:
        # 礼貌池邮箱：带上可获得更稳定的速率限额，可留空
        self._mailto = mailto

    def search(self, query: str, max_results: int = 5) -> list[Paper]:
        """按关键词检索 OpenAlex，按相关性排序。"""
        params = {"search": query, "per-page": str(max_results)}
        if self._mailto:
            params["mailto"] = self._mailto
        data = get_json(_API_URL, params=params)
        return [_to_paper(item) for item in data.get("results", [])]


def _to_paper(item: dict) -> Paper:
    """把 OpenAlex work 记录映射为 Paper。"""
    doi = (item.get("doi") or "").removeprefix(_DOI_PREFIX).lower()
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    # 无 DOI 时用 OpenAlex 短 id（如 W2100837269）作为唯一标识
    openalex_id = (item.get("id") or "").rsplit("/", 1)[-1]
    return Paper(
        uid=doi or openalex_id,
        title=normalize_ws(item.get("display_name") or ""),
        authors=clean_authors(
            authorship.get("author", {}).get("display_name", "")
            for authorship in item.get("authorships", [])
        ),
        year=item.get("publication_year") or 0,
        abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
        url=item.get("doi") or item.get("id") or "",
        tags=[c.get("display_name", "") for c in (item.get("concepts") or [])[:3]],
        citations=item.get("cited_by_count") or 0,
        venue=source.get("display_name") or "",
    )


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    """把 OpenAlex 的倒排索引摘要重建为原文文本；缺失时返回空串。"""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = [
        (index, word) for word, indexes in inverted.items() for index in indexes
    ]
    return " ".join(word for _, word in sorted(positions))
