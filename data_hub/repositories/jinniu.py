from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any

from database.db import get_connection
from data_hub.providers.jinniu_provider import canonical_hash, parse_timestamp


def _compact_date(value: Any) -> date | None:
    parsed = parse_timestamp(value)
    return parsed.date() if parsed else None


def _boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    return None


def natural_key(endpoint: str, data: Mapping[str, Any]) -> str:
    symbol = data.get("ts_code") or data.get("symbol")
    if endpoint in {"daily", "index_daily", "etf_daily", "adj_factor"}:
        values = [symbol, data.get("trade_date")]
    elif endpoint in {"stk_mins", "etf_mins", "idx_mins"}:
        values = [
            symbol,
            data.get("trade_time") or data.get("datetime"),
            data.get("freq"),
        ]
    elif endpoint == "trade_cal":
        values = [data.get("exchange"), data.get("cal_date")]
    elif endpoint == "stock_st":
        values = [symbol, data.get("trade_date")]
    elif endpoint == "namechange":
        values = [symbol, data.get("start_date"), data.get("name")]
    else:
        values = [symbol, data.get("list_status")]
    return canonical_hash([endpoint, *values])


class JinniuShadowRepository:
    def __init__(
        self,
        connection_factory: Callable[..., Any] = get_connection,
    ) -> None:
        self.connection_factory = connection_factory

    def persist(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows: list[list[Any]] = []
        for record in records:
            endpoint = str(record["endpoint"])
            data = dict(record["data"])
            key = natural_key(endpoint, data)
            content_hash = str(record["content_hash"])
            trade_value = data.get("trade_date") or data.get("cal_date")
            bar_value = data.get("trade_time") or data.get("datetime")
            if trade_value is None:
                # Minute bars carry no trade_date field; derive it from the
                # bar timestamp so downstream day-scoped queries work.
                trade_value = bar_value
            rows.append(
                [
                    f"jinniu_{canonical_hash([endpoint, key, content_hash])[:32]}",
                    endpoint,
                    key,
                    record.get("symbol"),
                    _compact_date(trade_value),
                    parse_timestamp(bar_value),
                    data.get("open"),
                    data.get("high"),
                    data.get("low"),
                    data.get("close"),
                    data.get("volume_shares"),
                    data.get("amount_cny"),
                    data.get("adj_factor"),
                    _boolean(data.get("is_open")),
                    record["provider"],
                    record["source_timestamp"],
                    record["available_time"],
                    record["fetched_at"],
                    record["data_cutoff"],
                    content_hash,
                    record["quality"],
                    record["schema_version"],
                    json.dumps(data, ensure_ascii=False, default=str),
                ]
            )
        if not rows:
            return 0
        with self.connection_factory() as connection:
            before = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jinniu_shadow_records"
                ).fetchone()[0]
            )
            connection.executemany(
                """
                INSERT INTO jinniu_shadow_records VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
            after = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jinniu_shadow_records"
                ).fetchone()[0]
            )
        return after - before

    def save_checkpoint(
        self,
        *,
        endpoint: str,
        scope: Mapping[str, Any],
        status: str,
        cursor: Mapping[str, Any],
        persisted_count: int,
        request_count: int,
        updated_at: datetime,
        error_type: str | None = None,
    ) -> str:
        scope_hash = canonical_hash(scope)
        checkpoint_id = f"jcp_{canonical_hash([endpoint, scope_hash])[:32]}"
        with self.connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO jinniu_import_checkpoints VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (endpoint, scope_hash) DO UPDATE SET
                    status = excluded.status,
                    cursor_json = excluded.cursor_json,
                    persisted_count = excluded.persisted_count,
                    request_count = excluded.request_count,
                    updated_at = excluded.updated_at,
                    error_type = excluded.error_type
                """,
                [
                    checkpoint_id,
                    endpoint,
                    scope_hash,
                    status,
                    json.dumps(cursor, ensure_ascii=False, default=str),
                    persisted_count,
                    request_count,
                    updated_at,
                    error_type,
                ],
            )
        return checkpoint_id

    def load_checkpoint(
        self,
        endpoint: str,
        scope: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        scope_hash = canonical_hash(scope)
        with self.connection_factory(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT status, cursor_json, persisted_count, request_count,
                       updated_at, error_type
                FROM jinniu_import_checkpoints
                WHERE endpoint = ? AND scope_hash = ?
                """,
                [endpoint, scope_hash],
            ).fetchone()
        if row is None:
            return None
        return {
            "status": row[0],
            "cursor": json.loads(row[1]),
            "persisted_count": int(row[2]),
            "request_count": int(row[3]),
            "updated_at": row[4],
            "error_type": row[5],
        }

    def persist_comparisons(
        self,
        comparisons: Iterable[Mapping[str, Any]],
    ) -> int:
        rows: list[list[Any]] = []
        for item in comparisons:
            identity = {
                key: item.get(key)
                for key in (
                    "run_id",
                    "endpoint",
                    "symbol",
                    "observation_time",
                    "comparison_source",
                    "field_name",
                )
            }
            rows.append(
                [
                    f"jcmp_{canonical_hash(identity)[:32]}",
                    item["run_id"],
                    item["endpoint"],
                    item.get("symbol"),
                    item["observation_time"],
                    item["comparison_source"],
                    item["field_name"],
                    item.get("jinniu_value"),
                    item.get("comparison_value"),
                    item.get("absolute_difference"),
                    item.get("relative_difference"),
                    item.get("matched"),
                    item.get("quality", "COMPLETE"),
                    json.dumps(
                        item.get("payload", {}),
                        ensure_ascii=False,
                        default=str,
                    ),
                ]
            )
        if not rows:
            return 0
        with self.connection_factory() as connection:
            connection.executemany(
                """
                INSERT INTO jinniu_shadow_comparisons VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) ON CONFLICT DO NOTHING
                """,
                rows,
            )
        return len(rows)

    def record_request(
        self,
        *,
        run_id: str,
        endpoint: str,
        status: str,
        record_count: int,
        latency_ms: int,
        truncated: bool,
        retry_count: int,
        requested_at: datetime,
        completed_at: datetime,
        request_hash: str,
        http_status: int | None = None,
        error_type: str | None = None,
    ) -> None:
        identity = [run_id, endpoint, request_hash, requested_at]
        with self.connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO jinniu_request_audits VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) ON CONFLICT DO NOTHING
                """,
                [
                    f"jreq_{canonical_hash(identity)[:32]}",
                    run_id,
                    endpoint,
                    status,
                    http_status,
                    record_count,
                    latency_ms,
                    truncated,
                    retry_count,
                    error_type,
                    requested_at,
                    completed_at,
                    request_hash,
                ],
            )

    def audit_metrics(self, run_id: str | None = None) -> dict[str, Any]:
        where = "WHERE run_id = ?" if run_id else ""
        parameters = [run_id] if run_id else []
        with self.connection_factory(read_only=True) as connection:
            requests = connection.execute(
                f"""
                SELECT endpoint,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END),
                       AVG(latency_ms), MAX(latency_ms),
                       SUM(CASE WHEN truncated THEN 1 ELSE 0 END),
                       SUM(CASE WHEN http_status = 401 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN http_status = 403 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN http_status = 405 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN http_status = 429 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN http_status = 503 THEN 1 ELSE 0 END),
                       MAX(error_type)
                FROM jinniu_request_audits
                {where}
                GROUP BY endpoint ORDER BY endpoint
                """,
                parameters,
            ).fetchall()
            coverage = connection.execute(
                """
                SELECT endpoint, COUNT(*), COUNT(DISTINCT symbol),
                       MIN(trade_date), MAX(trade_date),
                       SUM(CASE WHEN quality != 'COMPLETE' THEN 1 ELSE 0 END)
                FROM jinniu_shadow_records
                GROUP BY endpoint ORDER BY endpoint
                """
            ).fetchall()
            comparison_where = "WHERE run_id = ?" if run_id else ""
            comparisons = connection.execute(
                f"""
                SELECT field_name, COUNT(*),
                       SUM(CASE WHEN matched THEN 1 ELSE 0 END),
                       AVG(absolute_difference), AVG(relative_difference),
                       SUM(CASE WHEN comparison_value IS NULL THEN 1 ELSE 0 END)
                FROM jinniu_shadow_comparisons
                {comparison_where}
                GROUP BY field_name ORDER BY field_name
                """,
                parameters,
            ).fetchall()
        return {
            "requests": requests,
            "coverage": coverage,
            "comparisons": comparisons,
        }

    def route_gate(self) -> dict[str, Any]:
        with self.connection_factory(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT validated, audit_run_id, validated_at,
                       primary_provider, fallback_provider,
                       validation_provider, reason
                FROM jinniu_route_gate
                WHERE gate_name = 'HISTORICAL_MARKET'
                """
            ).fetchone()
        return {
            "validated": bool(row[0]),
            "audit_run_id": row[1],
            "validated_at": row[2],
            "primary": row[3],
            "fallback": row[4],
            "validation": row[5],
            "reason": row[6],
        }

    def set_route_gate(
        self,
        *,
        validated: bool,
        audit_run_id: str,
        at: datetime,
        reason: str,
    ) -> None:
        with self.connection_factory() as connection:
            connection.execute(
                """
                UPDATE jinniu_route_gate
                SET validated = ?, audit_run_id = ?, validated_at = ?,
                    reason = ?
                WHERE gate_name = 'HISTORICAL_MARKET'
                """,
                [validated, audit_run_id, at, reason],
            )


__all__ = ["JinniuShadowRepository", "natural_key"]
