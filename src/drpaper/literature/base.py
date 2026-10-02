"""文献层公开接口：论文数据模型与检索源协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Paper:
    """一条真实文献的元数据（只能来自检索 API，不允许模型编造）。"""

    uid: str  # 唯一标识，如 arXiv ID
    title: str
    authors: list[str]
    year: int
    abstract: str
    url: str
    tags: list[str] = field(default_factory=list)

    def reference_line(self, index: int) -> str:
        """生成 GB/T 7714 风格的参考文献条目。"""
        authors = ", ".join(self.authors[:3]) + (" et al." if len(self.authors) > 3 else "")
        year = f" ({self.year})." if self.year > 0 else "."
        return f"[{index}] {authors}. {self.title}[EB/OL]. {year} {self.url}"


class SearchProvider(Protocol):
    """文献检索源协议：新增数据源（如 Semantic Scholar）实现此协议即可。"""

    name: str

    def search(self, query: str, max_results: int) -> list[Paper]:
        """按关键词检索，返回真实元数据列表。"""
        ...
