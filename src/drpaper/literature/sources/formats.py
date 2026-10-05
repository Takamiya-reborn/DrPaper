"""本地文献文件解析：BibTeX / RIS / JSONL → Paper。

纯文本元数据解析，无网络无 LLM。三类格式的解析器各自独立，
解析失败抛异常由调用方（local.py）收集为警告；title 缺失的条目跳过。
uid 规则：有 DOI 用 DOI（可与在线源自动去重合并），否则 local:<标题哈希>。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from drpaper.literature.base import Paper, clean_authors, normalize_ws

# 文件后缀 → 解析函数
_PARSERS = {}

# 查询/标题归一化共用的非字母数字清理（uid 兜底哈希用）
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def parse_file(path: Path) -> list[Paper]:
    """按文件后缀分发解析；不支持的后缀抛 ValueError。"""
    parser = _PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"不支持的文献格式 {path.suffix}（支持 .bib / .ris / .jsonl）")
    return parser(path.read_text(encoding="utf-8"))


# ---- 公共原语 ----


def make_paper(
    *,
    title: str,
    authors: list[str],
    year: int,
    abstract: str = "",
    url: str = "",
    uid: str = "",
    tags: list[str] | None = None,
    venue: str = "",
    citations: int = 0,
) -> Paper | None:
    """组装 Paper：title 必填，uid 缺省用归一化标题哈希兜底。"""
    title = normalize_ws(title)
    if not title:
        return None
    if not uid:
        digest = hashlib.sha1(_NON_ALNUM.sub("", title.lower()).encode()).hexdigest()[
            :12
        ]
        uid = f"local:{digest}"
    return Paper(
        uid=uid,
        title=title,
        authors=clean_authors(authors),
        year=max(year, 0),
        abstract=normalize_ws(abstract),
        url=url.strip(),
        tags=tags or [],
        citations=max(citations, 0),
        venue=normalize_ws(venue),
    )


def first_year(text: str) -> int:
    """从文本中取首个 4 位数字作年份（RIS 的 Y1 常为 2023/01/01 之类）。"""
    match = re.search(r"\d{4}", text)
    return int(match.group()) if match else 0


# ---- BibTeX ----

_BIB_HEAD = re.compile(r"@\w+\s*\{", re.IGNORECASE)


def parse_bibtex(text: str) -> list[Paper]:
    """解析 @entry{key, field = {value}, ...}；花括号配对扫描，容忍嵌套。"""
    papers = []
    pos = 0
    while head := _BIB_HEAD.search(text, pos):
        start = head.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        paper = _bib_entry(text[start : i - 1])
        if paper is not None:
            papers.append(paper)
        pos = i
    return papers


def _bib_entry(body: str) -> Paper | None:
    """一个条目的花括号内文本 → Paper；首段是 citation key，忽略。"""
    fields: dict[str, str] = {}
    for part in _split_top_level(body)[1:]:
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        fields[name.strip().lower()] = _bib_value(value)
    title = fields.get("title", "")
    if not title:
        return None
    return make_paper(
        title=title,
        authors=re.split(r"\s+and\s+", fields.get("author", ""), flags=re.IGNORECASE),
        year=first_year(fields.get("year", "")),
        abstract=fields.get("abstract")
        or fields.get("summary")
        or fields.get("annote", ""),
        url=fields.get("url", ""),
        uid=fields.get("doi", ""),
        tags=_split_keywords(fields.get("keywords", "")),
        venue=fields.get("journal")
        or fields.get("booktitle")
        or fields.get("publisher", ""),
    )


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切段：花括号内与引号内的逗号不切分。"""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    for ch in body:
        if ch == '"' and depth == 0:
            in_quote = not in_quote
        elif ch == "{" and not in_quote:
            depth += 1
        elif ch == "}" and not in_quote:
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0 and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _bib_value(value: str) -> str:
    """去外层花括号/引号与内嵌保护花括号，折叠空白。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '{"':
        value = value[1:-1]
    return normalize_ws(value.replace("{", "").replace("}", ""))


def _split_keywords(raw: str) -> list[str]:
    """关键词字段按逗号/分号拆分为列表。"""
    return [kw.strip() for kw in re.split(r"[,;]", raw) if kw.strip()]


# ---- RIS ----

_RIS_TAG = re.compile(r"^([A-Z][A-Z0-9])\s*-\s?(.*)$")


def parse_ris(text: str) -> list[Paper]:
    """解析 TY/ER 定界的 RIS 记录；无标签的续行忽略。"""
    papers = []
    tags: list[tuple[str, str]] | None = None
    for line in text.splitlines():
        match = _RIS_TAG.match(line.strip())
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY":
            tags = []
        elif tag == "ER":
            if tags is not None:
                paper = _ris_paper(tags)
                if paper is not None:
                    papers.append(paper)
            tags = None
        elif tags is not None:
            tags.append((tag, value))
    return papers


def _ris_paper(tags: list[tuple[str, str]]) -> Paper | None:
    """一条 RIS 记录的标签对 → Paper。"""

    def first(*names: str) -> str:
        for tag, value in tags:
            if tag in names and value:
                return value
        return ""

    title = first("TI", "T1")
    if not title:
        return None
    return make_paper(
        title=title,
        authors=[value for tag, value in tags if tag in ("AU", "A1") and value],
        year=first_year(first("PY", "Y1")),
        abstract=first("AB", "N2"),
        url=first("UR"),
        uid=first("DO"),
        tags=[value for tag, value in tags if tag == "KW" and value],
        venue=first("JO", "JF", "T2", "TA", "JA"),
    )


# ---- JSONL ----


def parse_jsonl(text: str) -> list[Paper]:
    """每行一个 JSON 对象；字段宽松映射，坏行跳过。

    这是"知识库接口"的最小形态：一行一条文献记录，可由任意脚本
    （Zotero API、内部数据库）生成后直接挂载。
    """
    papers = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        paper = _json_paper(raw) if isinstance(raw, dict) else None
        if paper is not None:
            papers.append(paper)
    return papers


def _json_paper(raw: dict) -> Paper | None:
    """JSON 记录 → Paper；title 缺失跳过，数值字段不合法取 0。"""
    title = str(raw.get("title", "")).strip()
    if not title:
        return None
    authors = raw.get("authors") or []
    if isinstance(authors, str):
        authors = re.split(r"[,;、]", authors)
    return make_paper(
        title=title,
        authors=[str(a) for a in authors],
        year=_as_int(raw.get("year")),
        abstract=str(raw.get("abstract") or ""),
        url=str(raw.get("url") or ""),
        uid=str(raw.get("doi") or raw.get("uid") or ""),
        tags=[str(t) for t in (raw.get("tags") or raw.get("keywords") or [])],
        venue=str(raw.get("venue") or ""),
        citations=_as_int(raw.get("citations")),
    )


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


_PARSERS.update({".bib": parse_bibtex, ".ris": parse_ris, ".jsonl": parse_jsonl})
