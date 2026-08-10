from __future__ import annotations

import hashlib
import json
from datetime import datetime
from time import perf_counter
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from config.network import configure_network_policy
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import FinanceNewsResponse, ProviderRun
from data_hub.services.event_cluster_service import EventClusterService


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_symbol(value: str) -> tuple[str, str]:
    normalized = value.strip().upper()
    code = normalized.split(".", 1)[0]

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if "." in normalized:
        exchange = normalized.split(".", 1)[1]
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    elif code.startswith(("4", "8")):
        exchange = "BJ"
    else:
        exchange = "SZ"

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    return code, f"{code}.{exchange}"


def _clean_text(value: object) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def _event_time(value: object) -> datetime:
    timestamp = pd.to_datetime(value, errors="coerce")

    if pd.isna(timestamp):
        raise ValueError(f"无效新闻发布时间：{value}")

    parsed = pd.Timestamp(timestamp).to_pydatetime()

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI_TZ)

    return parsed.astimezone(SHANGHAI_TZ)


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)

    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
    )


def _content_hash(
    symbol: str,
    title: str,
    published_at: str,
    source_url: str,
) -> str:
    canonical = json.dumps(
        {
            "source": "Eastmoney News via AKShare",
            "symbol": symbol,
            "data_type": DataType.FINANCE_NEWS.value,
            "title": title,
            "published_at": published_at,
            "source_url": source_url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FinanceNewsService:
    """Fetch, normalize, and persist stock-related media reports."""

    def __init__(self) -> None:
        configure_network_policy()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def _fetch(self, query: str) -> pd.DataFrame:
        import akshare as ak
        return ak.stock_news_em(symbol=query)

    @staticmethod
    def _persist(records: list[MarketRecord]) -> None:
        initialize_database()

        with get_connection() as connection:
            for record in records:
                existing = connection.execute(
                    """
                    SELECT record_id
                    FROM data_records
                    WHERE content_hash = ?
                    LIMIT 1
                    """,
                    [record.content_hash],
                ).fetchone()

                if existing is None:
                    insert_market_record(connection, record)
        if records:
            EventClusterService().cluster(records)

    def get_finance_news(
        self,
        symbol: str,
        query: str | None = None,
        limit: int = 100,
        persist: bool = True,
    ) -> FinanceNewsResponse:
        code, canonical_symbol = _normalize_symbol(symbol)
        actual_query = (query or code).strip()

        if not actual_query:
            raise ValueError("新闻查询关键词不能为空")

        if limit < 1 or limit > 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")

        started = perf_counter()

        try:
            frame = self._fetch(actual_query)
        except Exception as exc:
            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            raise RuntimeError(
                "东方财富个股新闻接口失败："
                f"{type(exc).__name__}: {exc}; "
                f"latency={latency_ms}ms"
            ) from exc

        required = {
            "关键词",
            "新闻标题",
            "新闻内容",
            "发布时间",
            "文章来源",
            "新闻链接",
        }

        missing = required.difference(frame.columns)

        if missing:
            raise RuntimeError(
                f"东方财富新闻接口缺少字段：{sorted(missing)}"
            )

        frame = frame.copy()
        frame["发布时间"] = pd.to_datetime(
            frame["发布时间"],
            errors="coerce",
        )

        frame = frame.sort_values(
            "发布时间",
            ascending=False,
            na_position="last",
        ).head(limit)

        records: list[MarketRecord] = []

        for _, row in frame.iterrows():
            title = _clean_text(row.get("新闻标题"))
            content = _clean_text(row.get("新闻内容"))
            publisher = _clean_text(row.get("文章来源"))
            source_url = _clean_text(row.get("新闻链接"))
            raw_keyword = _clean_text(row.get("关键词"))

            if not title or not source_url:
                continue

            if not _valid_http_url(source_url):
                continue

            try:
                event_time = _event_time(row.get("发布时间"))
            except ValueError:
                continue

            published_at = event_time.isoformat()

            payload: dict[str, object] = {
                "symbol": canonical_symbol,
                "query": actual_query,
                "raw_keyword": raw_keyword,
                "title": title,
                "content": content,
                "published_at": published_at,
                "publisher": publisher,
                "url": source_url,
                "verification_status": "unverified_media",
            }

            records.append(
                MarketRecord(
                    symbol=canonical_symbol,
                    data_type=DataType.FINANCE_NEWS,
                    event_time=event_time,
                    source_name="Eastmoney News via AKShare",
                    source_url=source_url,
                    source_level=SourceLevel.MEDIA,
                    verified=False,
                    content_hash=_content_hash(
                        symbol=canonical_symbol,
                        title=title,
                        published_at=published_at,
                        source_url=source_url,
                    ),
                    data=payload,
                )
            )

        latency_ms = round((perf_counter() - started) * 1000)

        provider_run = ProviderRun(
            provider="Eastmoney News via AKShare",
            success=True,
            record_count=len(records),
            latency_ms=latency_ms,
        )

        if persist:
            self._persist(records)

        return FinanceNewsResponse(
            symbol=canonical_symbol,
            query=actual_query,
            records=records,
            provider_runs=[provider_run],
            unverified_count=sum(
                1 for record in records if not record.verified
            ),
        )
