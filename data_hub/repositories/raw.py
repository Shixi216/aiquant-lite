from __future__ import annotations

import json

from database.db import get_connection
from data_hub.schemas.market import MarketRecord


def _row_to_record(row: tuple[object, ...]) -> MarketRecord:
    payload = json.loads(row[10]) if isinstance(row[10], str) else row[10]
    return MarketRecord(
        record_id=row[0],
        symbol=row[1],
        data_type=row[2],
        event_time=row[3],
        fetched_at=row[4],
        source_name=row[5],
        source_url=row[6],
        source_level=row[7],
        verified=row[8],
        content_hash=row[9],
        data=payload,
    )


def load_raw_records(record_ids: list[str]) -> list[MarketRecord]:
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        return []
    placeholders = ", ".join("?" for _ in unique_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT record_id, symbol, data_type, event_time, fetched_at,
                   source_name, source_url, source_level, verified,
                   content_hash, payload_json
            FROM data_records
            WHERE record_id IN ({placeholders})
            """,
            unique_ids,
        ).fetchall()
    by_id = {row[0]: _row_to_record(row) for row in rows}
    return [
        by_id[record_id]
        for record_id in unique_ids
        if record_id in by_id
    ]


def resolve_persisted_raw_records(
    records: list[MarketRecord],
) -> list[MarketRecord]:
    """Resolve repeat fetches to the original persisted record IDs."""

    resolved: list[MarketRecord] = []
    with get_connection() as connection:
        for record in records:
            row = connection.execute(
                """
                SELECT record_id, symbol, data_type, event_time, fetched_at,
                       source_name, source_url, source_level, verified,
                       content_hash, payload_json
                FROM data_records
                WHERE record_id = ?
                   OR (content_hash IS NOT NULL AND content_hash = ?)
                ORDER BY CASE WHEN record_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                [record.record_id, record.content_hash, record.record_id],
            ).fetchone()
            resolved.append(_row_to_record(row) if row is not None else record)
    return resolved


__all__ = ["load_raw_records", "resolve_persisted_raw_records"]
