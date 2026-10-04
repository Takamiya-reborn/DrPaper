"""检索源共用 HTTP 客户端：GET JSON + 限流退避重试。

各检索源（OpenAlex、Semantic Scholar）的 API 都是普通 GET 返回 JSON，
统一走这里，避免每个源各自处理超时、429 退避等细节。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# 可重试状态码：限流与服务端临时故障
_RETRYABLE_CODES = {429, 500, 502, 503, 504}

# 最大尝试次数（含首次）
_MAX_ATTEMPTS = 4

# 单次请求超时（秒）
_TIMEOUT_SECONDS = 15

# 限流(429)重试的最小等待秒数：免费档 API 的限流窗口通常以秒计
_RATE_LIMIT_MIN_WAIT = 5

# 所有请求带上的 UA，便于 API 方识别来源
_USER_AGENT = "DrPaper/1.0 (academic writing agent)"


def get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET 请求并解析 JSON；可重试错误按指数退避，其余直接抛出。"""
    query = urllib.parse.urlencode(params or {})
    request = urllib.request.Request(
        f"{url}?{query}" if query else url,
        headers={"User-Agent": _USER_AGENT, **(headers or {})},
    )
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_CODES:
                raise
            last_error = exc
            _backoff(exc, attempt)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            _backoff(None, attempt)
    assert last_error is not None
    raise last_error


def _backoff(error: urllib.error.HTTPError | None, attempt: int) -> None:
    """按 Retry-After 头或指数退避等待后重试；限流(429)至少等 5 秒。"""
    delay = 2**attempt
    if error is not None:
        if error.code == 429:
            delay = max(delay, _RATE_LIMIT_MIN_WAIT)
        retry_after = error.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            delay = max(delay, int(retry_after))
    time.sleep(delay)
