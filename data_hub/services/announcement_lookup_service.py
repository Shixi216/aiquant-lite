from __future__ import annotations

import json
import re
from typing import Any

from data_hub.schemas.market import MarketRecord
from data_hub.services.announcement_service import (
    _normalize_symbol,
)
from database.db import get_connection


ANNOUNCEMENT_ID_PATTERN = re.compile(
    r"^[0-9]{6,30}$"
)


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "本地公告 payload_json 不是合法 JSON"
            ) from exc

        if isinstance(decoded, dict):
            return decoded

    raise RuntimeError(
        "本地公告 payload_json 不是 JSON 对象"
    )


def _row_to_record(
    row: tuple[Any, ...],
) -> MarketRecord:
    (
        record_id,
        symbol,
        data_type,
        event_time,
        fetched_at,
        source_name,
        source_url,
        source_level,
        verified,
        content_hash,
        payload_json,
    ) = row

    return MarketRecord.model_validate(
        {
            "record_id": record_id,
            "symbol": symbol,
            "data_type": data_type,
            "event_time": event_time,
            "fetched_at": fetched_at,
            "source_name": source_name,
            "source_url": source_url,
            "source_level": source_level,
            "verified": verified,
            "content_hash": content_hash,
            "data": _decode_payload(payload_json),
        }
    )


class AnnouncementLookupService:
    """Resolve persisted official announcements without date windows."""

    def resolve(
        self,
        *,
        symbol: str,
        record_id: str | None = None,
        announcement_id: str | None = None,
    ) -> MarketRecord:
        _, canonical_symbol = _normalize_symbol(symbol)

        normalized_record_id = (
            record_id.strip()
            if record_id is not None
            else None
        )

        normalized_announcement_id = (
            announcement_id.strip()
            if announcement_id is not None
            else None
        )

        if (
            not normalized_record_id
            and not normalized_announcement_id
        ):
            raise ValueError(
                "必须提供 record_id 或 announcement_id"
            )

        if (
            normalized_announcement_id is not None
            and not ANNOUNCEMENT_ID_PATTERN.fullmatch(
                normalized_announcement_id
            )
        ):
            raise ValueError(
                "announcement_id 格式不合法"
            )

        conditions = [
            "symbol = ?",
            "data_type = 'announcement'",
            "verified = TRUE",
            "source_level = 'official'",
        ]
        parameters: list[Any] = [
            canonical_symbol,
        ]

        if normalized_record_id:
            conditions.append("record_id = ?")
            parameters.append(normalized_record_id)

        if normalized_announcement_id:
            conditions.append(
                """
                regexp_extract(
                    COALESCE(source_url, ''),
                    'announcementId=([0-9]+)',
                    1
                ) = ?
                """
            )
            parameters.append(
                normalized_announcement_id
            )

        where_clause = " AND ".join(conditions)

        connection = get_connection()

        try:
            rows = connection.execute(
                f"""
                SELECT
                    record_id,
                    symbol,
                    data_type,
                    event_time,
                    fetched_at,
                    source_name,
                    source_url,
                    source_level,
                    verified,
                    content_hash,
                    payload_json
                FROM data_records
                WHERE {where_clause}
                ORDER BY fetched_at DESC
                LIMIT 2
                """,
                parameters,
            ).fetchall()
        finally:
            connection.close()

        if not rows:
            raise LookupError(
                "本地数据库中没有找到匹配的"
                "已核验官方公告"
            )

        if len(rows) > 1:
            raise RuntimeError(
                "公告选择器匹配到多条本地记录，"
                "请同时提供 record_id 和 announcement_id"
            )

        return _row_to_record(rows[0])
