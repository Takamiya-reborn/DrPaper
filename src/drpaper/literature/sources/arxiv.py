"""arXiv 检索源实现。"""

from __future__ import annotations

import arxiv

from drpaper.literature.base import Paper, normalize_ws


class ArxivProvider:
    """基于 arXiv 官方 API 的检索实现。"""

    name = "arxiv"

    def search(self, query: str, max_results: int = 5) -> list[Paper]:
        """按关键词检索 arXiv，按相关性排序。"""
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        papers: list[Paper] = []
        for result in client.results(search):
            published = result.published
            papers.append(
                Paper(
                    uid=result.get_short_id(),
                    title=normalize_ws(result.title),
                    authors=[a.name for a in result.authors],
                    year=published.year if published else 0,
                    abstract=normalize_ws(result.summary),
                    url=result.pdf_url or str(result.entry_id),
                    tags=[c for c in result.categories[:3]],
                )
            )
        return papers
