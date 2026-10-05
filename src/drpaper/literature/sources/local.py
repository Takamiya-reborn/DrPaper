"""本地文献库检索源：用户外接的文献文件（BibTeX / RIS / JSONL）。

用户文献优先级最高，由聚合器落实（见 aggregator.py）：
本源排 tier 0 最先检索，结果固定前置不被截断淘汰，
与在线源同键去重时保留用户版元数据。

匹配是纯本地关键词打分：查询词命中标题/摘要/作者/venue/关键词的
占比越高越靠前，无网络无 LLM，文件在构造时一次性加载。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from drpaper.literature.base import Paper
from drpaper.literature.sources.formats import parse_file

# 目录扫描时收集的文献文件后缀
_SUFFIXES = {".bib", ".ris", ".jsonl"}

# 查询分词：拉丁字母数字串或连续汉字段
_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]+")


class LocalLibraryProvider:
    """实现 SearchProvider 协议的本地文献库；priority 标记给聚合器高优待遇。"""

    name = "local_library"
    priority = True  # 聚合器据此固定前置检索结果、合并冲突时保留用户版

    def __init__(self, paths: tuple[str, ...] = ()) -> None:
        self.papers: list[Paper] = []
        self.warnings: list[str] = []
        self._haystacks: list[str] = []
        self._seen: set[str] = (
            set()
        )  # uid 去重：同一文件经目录+显式路径重复传入只加载一次
        for raw in paths:
            self._load(Path(raw).expanduser())

    def _load(self, path: Path) -> None:
        """加载单个文件或目录（递归收集 .bib/.ris/.jsonl）。"""
        if not path.exists():
            self.warnings.append(f"文献库路径不存在，已跳过：{path}")
            return
        if path.is_dir():
            files = sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in _SUFFIXES
            )
            if not files:
                self.warnings.append(
                    f"文献库目录中没有可识别的文献文件（.bib/.ris/.jsonl）：{path}"
                )
            for file in files:
                self._load_file(file)
        else:
            self._load_file(path)

    def _load_file(self, path: Path) -> None:
        try:
            papers = parse_file(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.warnings.append(f"文献文件解析失败，已跳过：{path}\n  {exc}")
            return
        for paper in papers:
            if paper.uid in self._seen:
                continue  # 同一文件经目录+显式路径重复传入只保留一份
            self._seen.add(paper.uid)
            self.papers.append(paper)
            self._haystacks.append(self._haystack(paper))

    @staticmethod
    def _haystack(paper: Paper) -> str:
        """检索用文本：标题/venue/作者/摘要/关键词拼合后小写。"""
        return " ".join(
            [
                paper.title,
                paper.venue,
                " ".join(paper.authors),
                paper.abstract,
                " ".join(paper.tags),
            ]
        ).lower()

    def search(self, query: str, max_results: int) -> list[Paper]:
        """关键词打分检索：按命中占比排序，返回结果全部标 origin=user。"""
        tokens = _TOKEN.findall(query.lower())
        if not tokens or not self.papers:
            return []
        scored = []
        for paper, haystack in zip(self.papers, self._haystacks):
            hits = sum(1 for token in tokens if token in haystack)
            if hits:
                scored.append((hits / len(tokens), hits, paper))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [replace(paper, origin="user") for _, _, paper in scored[:max_results]]
