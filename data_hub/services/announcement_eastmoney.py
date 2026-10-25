"""东财单股公告交叉源。

只接受单一股票代码查询；每域名请求间隔至少 1 秒，失败由上层静默降级。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LIST_ENDPOINT = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DETAIL_ENDPOINT = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
ALLOWED_DOMAINS = {
    "np-anotice-stock.eastmoney.com",
    "np-cnotice-stock.eastmoney.com",
    "data.eastmoney.com",
}
TIMEOUT_SECONDS = 5.0
MIN_REQUEST_INTERVAL = 1.0
MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 3
MAX_DETAIL_FETCHES = 5

_RATE_LOCK = threading.Lock()
_LAST_REQUEST: dict[str, float] = {}


@dataclass(frozen=True)
class EastmoneyAnnouncement:
    symbol: str
    title: str
    disclosure_time: datetime
    url: str
    raw_disclosure_time: str
    art_code: str
    content: str = ""


def _is_allowed_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return host in ALLOWED_DOMAINS


def _wait_for_domain(url: str, deadline: float | None = None) -> None:
    host = (urlparse(url).hostname or "").casefold()
    with _RATE_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST.get(host, 0.0)
        if elapsed < MIN_REQUEST_INTERVAL:
            delay = MIN_REQUEST_INTERVAL - elapsed
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise TimeoutError("东财公告总超时 5 秒")
            time.sleep(delay)
        _LAST_REQUEST[host] = time.monotonic()


def _parse_disclosure_time(value: str) -> datetime:
    text = str(value or "").strip()
    # 东财 display_time 使用 HH:MM:SS:mmm，转成 Python 可识别的小数秒。
    text = re.sub(r"(\d{2}:\d{2}:\d{2}):(\d{1,6})$", r"\1.\2", text)
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=SHANGHAI_TZ) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI_TZ)


def _normalize_title(value: str) -> str:
    text = str(value or "")
    if ":" in text[:20] or "：" in text[:20]:
        text = re.split(r"[:：]", text, maxsplit=1)[-1]
    text = re.sub(r"[\s:：，,。；;（）()《》\-—_]+", "", text)
    return text.replace("关于", "")


def title_similarity(left: str, right: str) -> float:
    a = _normalize_title(left)
    b = _normalize_title(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def best_title_match(
    title: str,
    candidates: list[EastmoneyAnnouncement],
    *,
    threshold: float = 0.65,
) -> EastmoneyAnnouncement | None:
    ranked = sorted(
        ((title_similarity(title, item.title), item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return ranked[0][1] if ranked and ranked[0][0] >= threshold else None


class EastmoneyAnnouncementService:
    """合规、低频的东财单股公告读取器。"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def _request_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        deadline: float | None = None,
    ) -> dict[str, Any]:
        if not _is_allowed_url(url):
            raise ValueError(f"东财公告域名不在白名单：{url}")
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "HermesOPC-AnnouncementBot/1.0 (+local research)",
                "Accept": "application/json",
            },
        )
        try:
            current = url
            current_params: dict[str, Any] | None = params
            for _ in range(MAX_REDIRECTS + 1):
                _wait_for_domain(current, deadline)
                request_timeout = TIMEOUT_SECONDS
                if deadline is not None:
                    request_timeout = deadline - time.monotonic()
                    if request_timeout <= 0:
                        raise TimeoutError("东财公告总超时 5 秒")
                response = client.get(
                    current,
                    params=current_params,
                    timeout=max(0.1, min(TIMEOUT_SECONDS, request_timeout)),
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    target = urljoin(current, location)
                    if not _is_allowed_url(target):
                        raise RuntimeError(f"东财公告重定向越过白名单：{target}")
                    current, current_params = target, None
                    continue
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("东财公告响应超过 1MB")
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type.casefold():
                    raise RuntimeError(f"东财公告响应类型异常：{content_type}")
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            raise RuntimeError("东财公告重定向次数超过限制")
        finally:
            if owns_client:
                client.close()

    def _fetch_detail(self, art_code: str, *, deadline: float) -> str:
        payload = self._request_json(
            DETAIL_ENDPOINT,
            params={"art_code": art_code, "client_source": "web", "page_index": 1},
            deadline=deadline,
        )
        data = payload.get("data") or {}
        return str(data.get("notice_content") or "")[:20_000]

    def fetch(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[EastmoneyAnnouncement]:
        code = str(symbol).split(".", 1)[0]
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"无效股票代码：{symbol}")
        start = datetime.strptime(start_date.replace("-", ""), "%Y%m%d").date()
        end = datetime.strptime(end_date.replace("-", ""), "%Y%m%d").date()
        deadline = time.monotonic() + TIMEOUT_SECONDS
        payload = self._request_json(
            LIST_ENDPOINT,
            params={
                "sr": -1,
                "page_size": 100,
                "page_index": 1,
                "ann_type": "A",
                "client_source": "web",
                "stock_list": code,
            },
            deadline=deadline,
        )
        rows = ((payload.get("data") or {}).get("list") or [])
        results: list[EastmoneyAnnouncement] = []
        detail_count = 0
        detail_markers = ("重要事项提示", "风险提示", "监管", "立案", "停产", "停业")
        for row in rows:
            codes = row.get("codes") or []
            row_codes = {str(item.get("stock_code") or "") for item in codes}
            if code not in row_codes:
                continue
            raw_time = str(row.get("display_time") or row.get("notice_date") or "")
            try:
                disclosure_time = _parse_disclosure_time(raw_time)
            except (TypeError, ValueError):
                continue
            if not start <= disclosure_time.date() <= end:
                continue
            title = str(row.get("title") or row.get("title_ch") or "").strip()
            art_code = str(row.get("art_code") or "").strip()
            if not title or not art_code:
                continue
            content = ""
            if detail_count < MAX_DETAIL_FETCHES and any(marker in title for marker in detail_markers):
                try:
                    content = self._fetch_detail(art_code, deadline=deadline)
                    detail_count += 1
                except Exception:
                    content = ""
            results.append(
                EastmoneyAnnouncement(
                    symbol=code,
                    title=title,
                    disclosure_time=disclosure_time,
                    raw_disclosure_time=raw_time,
                    art_code=art_code,
                    url=f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html",
                    content=content,
                )
            )
        return results


__all__ = [
    "EastmoneyAnnouncement",
    "EastmoneyAnnouncementService",
    "best_title_match",
    "title_similarity",
]
