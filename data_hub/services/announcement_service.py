from __future__ import annotations

import hashlib
import json
from datetime import datetime
from time import perf_counter
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from config.network import configure_network_policy
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import AnnouncementResponse, ProviderRun


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


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(
            f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD：{value}"
        )

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


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
        raise ValueError(f"无效公告时间：{value}")

    date_text = pd.Timestamp(timestamp).strftime("%Y%m%d")

    return datetime.strptime(
        date_text,
        "%Y%m%d",
    ).replace(
        hour=0,
        minute=0,
        second=0,
        tzinfo=SHANGHAI_TZ,
    )


def _is_cninfo_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").lower()

    return (
        host == "cninfo.com.cn"
        or host.endswith(".cninfo.com.cn")
    )


def _content_hash(
    symbol: str,
    title: str,
    announcement_time: str,
    source_url: str,
) -> str:
    canonical = json.dumps(
        {
            "source": "CNInfo via AKShare",
            "symbol": symbol,
            "data_type": DataType.ANNOUNCEMENT.value,
            "title": title,
            "announcement_time": announcement_time,
            "source_url": source_url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AnnouncementService:
    """Fetch and persist official listed-company announcements."""

    def __init__(self) -> None:
        configure_network_policy()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def _fetch(
        self,
        code: str,
        start_date: str,
        end_date: str,
        keyword: str,
        category: str,
    ) -> pd.DataFrame:
        return ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code,
            market="沪深京",
            keyword=keyword,
            category=category,
            start_date=start_date,
            end_date=end_date,
        )

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

    def get_announcements(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        keyword: str = "",
        category: str = "",
        persist: bool = True,
    ) -> AnnouncementResponse:
        code, canonical_symbol = _normalize_symbol(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        started = perf_counter()

        try:
            frame = self._fetch(
                code=code,
                start_date=start,
                end_date=end,
                keyword=keyword,
                category=category,
            )
        except Exception as exc:
            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            raise RuntimeError(
                "巨潮资讯公告接口失败："
                f"{type(exc).__name__}: {exc}; "
                f"latency={latency_ms}ms"
            ) from exc

        required = {
            "代码",
            "简称",
            "公告标题",
            "公告时间",
            "公告链接",
        }

        missing = required.difference(frame.columns)

        if missing:
            raise RuntimeError(
                f"巨潮资讯公告缺少字段：{sorted(missing)}"
            )

        frame = frame.copy()
        frame["公告时间"] = pd.to_datetime(
            frame["公告时间"],
            errors="coerce",
        )

        frame = frame.sort_values(
            "公告时间",
            ascending=False,
            na_position="last",
        )

        records: list[MarketRecord] = []

        for _, row in frame.iterrows():
            title = _clean_text(row.get("公告标题"))
            source_url = _clean_text(row.get("公告链接"))
            short_name = _clean_text(row.get("简称"))
            raw_code = _clean_text(row.get("代码"))

            if not title or not source_url:
                continue

            try:
                event_time = _event_time(row.get("公告时间"))
            except ValueError:
                continue

            official_url = _is_cninfo_url(source_url)
            announcement_time = event_time.strftime("%Y%m%d")

            payload: dict[str, object] = {
                "symbol": canonical_symbol,
                "raw_code": raw_code,
                "short_name": short_name,
                "title": title,
                "announcement_date": announcement_time,
                "url": source_url,
                "keyword": keyword,
                "category": category,
                "official_url": official_url,
            }

            records.append(
                MarketRecord(
                    symbol=canonical_symbol,
                    data_type=DataType.ANNOUNCEMENT,
                    event_time=event_time,
                    source_name="CNInfo via AKShare",
                    source_url=source_url,
                    source_level=(
                        SourceLevel.OFFICIAL
                        if official_url
                        else SourceLevel.PUBLIC_WEB
                    ),
                    verified=official_url,
                    content_hash=_content_hash(
                        symbol=canonical_symbol,
                        title=title,
                        announcement_time=announcement_time,
                        source_url=source_url,
                    ),
                    data=payload,
                )
            )

        latency_ms = round((perf_counter() - started) * 1000)

        provider_run = ProviderRun(
            provider="CNInfo via AKShare",
            success=True,
            record_count=len(records),
            latency_ms=latency_ms,
        )

        if persist:
            self._persist(records)

        return AnnouncementResponse(
            symbol=canonical_symbol,
            start_date=start,
            end_date=end,
            keyword=keyword,
            category=category,
            records=records,
            provider_runs=[provider_run],
            verified_count=sum(
                1 for record in records if record.verified
            ),
        )