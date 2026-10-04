"""消息历史维护：折叠被取代的旧分节正文。"""

from __future__ import annotations

import json
from typing import Any

# 历史消息中被取代的旧分节正文的折叠占位符
_FOLDED_SECTION = "（旧节内容已折叠，以最近一次提交为准）"


def fold_old_section_drafts(messages: list[dict[str, Any]], section_name: str) -> None:
    """把历史中该节被取代的旧提交折叠为占位符，只保留最近一次提交。

    在 submit_section 成功后调用：某节重写后，旧正文若留在历史里
    会被之后每一轮请求重复发送。保留每节最新一次提交，维持跨节行文衔接。
    """
    for msg in messages[:-1]:  # 末条 assistant 是本次提交，保留
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name") != "submit_section":
                continue
            try:
                args = json.loads(fn.get("arguments", ""))
            except json.JSONDecodeError:
                continue
            if str(args.get("name", "")) != section_name:
                continue
            content = args.get("content")
            if not isinstance(content, str) or content == _FOLDED_SECTION:
                continue  # 已折叠过
            # 保留 name 键，历史里才能看出是哪节
            fn["arguments"] = json.dumps(
                {"name": section_name, "content": _FOLDED_SECTION},
                ensure_ascii=False,
            )
