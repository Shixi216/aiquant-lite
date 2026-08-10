from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from config.settings import settings
from database.db import get_connection, initialize_database
from data_hub.schemas.market import MarketRecord


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _frame_insert(
    connection: Any,
    *,
    table: str,
    columns: list[str],
    rows: list[list[Any]],
    where: str | None = None,
) -> None:
    if not rows:
        return
    view = "_history_bulk_rows"
    quoted = ", ".join(f'"{column}"' for column in columns)
    connection.register(
        view,
        pd.DataFrame.from_records(rows, columns=columns),
    )
    try:
        condition = f" WHERE {where}" if where else ""
        connection.execute(
            f"""
            INSERT INTO "{table}" ({quoted})
            SELECT {quoted} FROM "{view}" AS incoming
            {condition}
            ON CONFLICT DO NOTHING
            """
        )
    finally:
        connection.unregister(view)


def _optional_number_matches(
    left: Any,
    right: Any,
    *,
    absolute_tolerance: float,
    relative_tolerance: float = 1e-9,
) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(
        float(left),
        float(right),
        abs_tol=absolute_tolerance,
        rel_tol=relative_tolerance,
    )


def _historical_values_match(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> bool:
    return all(
        (
            _optional_number_matches(
                existing[field],
                incoming[field],
                absolute_tolerance=tolerance,
            )
            for field, tolerance in (
                ("open", 1e-6),
                ("high", 1e-6),
                ("low", 1e-6),
                ("close", 1e-6),
                ("volume", 0.5),
                ("amount", 1.1),
            )
        )
    )


class HistoryRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = (
            database_path or settings.opc_database_path
        ).expanduser().resolve()
        if database_path is not None:
            raise ValueError(
                "database_path overrides are unsupported; configure "
                "settings.opc_database_path"
            )
        initialize_database()

    def database_bytes(self) -> int:
        return self.database_path.stat().st_size if self.database_path.exists() else 0

    def save_calendar(
        self,
        *,
        provider: str,
        rows: list[dict[str, Any]],
        data_cutoff: datetime,
        fetched_at: datetime,
    ) -> int:
        if not rows:
            return 0
        dates = [item["calendar_date"] for item in rows]
        with get_connection() as connection:
            before = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM trading_calendar_days
                    WHERE provider = ? AND calendar_date IN (
                        SELECT UNNEST(?::DATE[])
                    )
                    """,
                    [provider, dates],
                ).fetchone()[0]
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                _frame_insert(
                    connection,
                    table="trading_calendar_days",
                    columns=[
                        "provider",
                        "calendar_date",
                        "is_trading_day",
                        "data_cutoff",
                        "fetched_at",
                        "content_hash",
                        "source_metadata_json",
                    ],
                    rows=[
                        [
                            provider,
                            item["calendar_date"],
                            item["is_trading_day"],
                            data_cutoff,
                            fetched_at,
                            item["content_hash"],
                            _dump(item.get("metadata", {})),
                        ]
                        for item in rows
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return max(0, len(rows) - before)

    def trading_days(
        self,
        *,
        data_cutoff: datetime,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None,
    ) -> list[date]:
        clauses = ["is_trading_day = TRUE", "data_cutoff <= ?"]
        parameters: list[Any] = [data_cutoff]
        if start_date:
            clauses.append("calendar_date >= ?")
            parameters.append(start_date)
        if end_date:
            clauses.append("calendar_date <= ?")
            parameters.append(end_date)
        limit_sql = "LIMIT ?" if limit else ""
        if limit:
            parameters.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT calendar_date
                FROM trading_calendar_days
                WHERE {' AND '.join(clauses)}
                ORDER BY calendar_date DESC
                {limit_sql}
                """,
                parameters,
            ).fetchall()
        return sorted(row[0] for row in rows)

    def latest_completed_trade_date(
        self,
        data_cutoff: datetime,
    ) -> date | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT MAX(calendar_date)
                FROM trading_calendar_days
                WHERE is_trading_day = TRUE
                  AND data_cutoff <= ?
                  AND (
                    calendar_date < CAST(? AS DATE)
                    OR (
                        calendar_date = CAST(? AS DATE)
                        AND EXTRACT(hour FROM ?) >= 16
                    )
                  )
                """,
                [data_cutoff, data_cutoff, data_cutoff, data_cutoff],
            ).fetchone()
        return None if row is None else row[0]

    def select_universe(
        self,
        *,
        data_cutoff: datetime,
        symbols: list[str],
        board: str | None,
        exchange: str | None,
        start_symbol: str | None,
        end_symbol: str | None,
        liquidity_order: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = [
            "u.universe_version = ("
            "SELECT universe_version FROM stock_universe_versions "
            "WHERE data_cutoff <= ? ORDER BY effective_at DESC LIMIT 1)",
            "u.listing_status = 'ACTIVE'",
        ]
        parameters: list[Any] = [data_cutoff]
        if symbols:
            clauses.append("u.symbol IN (SELECT UNNEST(?::VARCHAR[]))")
            parameters.append(symbols)
        if board:
            clauses.append("u.board = ?")
            parameters.append(board.upper())
        if exchange:
            clauses.append("u.exchange = ?")
            parameters.append(exchange.upper())
        if start_symbol:
            clauses.append("u.symbol >= ?")
            parameters.append(start_symbol)
        if end_symbol:
            clauses.append("u.symbol <= ?")
            parameters.append(end_symbol)
        order = (
            "COALESCE(s.amount, -1) DESC, u.symbol"
            if liquidity_order
            else "u.symbol"
        )
        parameters.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                WITH latest_snapshot AS (
                    SELECT snapshot_id
                    FROM market_snapshot_runs
                    WHERE snapshot_time <= ?
                    ORDER BY snapshot_time DESC
                    LIMIT 1
                )
                SELECT
                    u.symbol, u.list_date, u.board, u.exchange,
                    COALESCE(s.is_suspended, FALSE) AS special_status,
                    s.amount
                FROM stock_universe u
                LEFT JOIN market_snapshot_items s
                  ON s.symbol = u.symbol
                 AND s.snapshot_id = (
                    SELECT snapshot_id FROM latest_snapshot
                 )
                WHERE {' AND '.join(clauses)}
                ORDER BY {order}
                LIMIT ?
                """,
                [data_cutoff, *parameters],
            ).fetchall()
        return [
            {
                "symbol": row[0],
                "list_date": row[1],
                "board": row[2],
                "exchange": row[3],
                "special_status": bool(row[4]),
                "amount": row[5],
            }
            for row in rows
        ]

    def create_run(
        self,
        *,
        run: dict[str, Any],
        shards: list[dict[str, Any]],
        items: list[dict[str, Any]],
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO historical_backfill_runs (
                        run_id, mode, provider, fallback_providers_json,
                        selection_strategy, requested_symbols_json,
                        requested_date_range_json, target_trading_days,
                        adjustment_type, eligible_symbol_count,
                        completed_symbol_count, successful_symbol_count,
                        skipped_symbol_count, failed_symbol_count,
                        request_count, request_budget, concurrency,
                        max_retries, retry_count, started_at, completed_at,
                        status, resume_cursor, database_bytes_before,
                        database_bytes_after, persisted_raw_count,
                        persisted_canonical_count, report_path, request_json,
                        error_summary_json
                    )
                    VALUES (
                        ?, 'APPLY', ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0,
                        0, ?, ?, ?, 0, ?, NULL, 'PENDING', 0, ?, NULL,
                        0, 0, ?, ?, '{}'
                    )
                    """,
                    [
                        run["run_id"],
                        run["provider"],
                        _dump(run["fallback_providers"]),
                        run["selection_strategy"],
                        _dump(run["requested_symbols"]),
                        _dump(run["requested_date_range"]),
                        run["target_trading_days"],
                        run["adjustment_type"],
                        run["eligible_symbol_count"],
                        run["initial_skipped_count"],
                        run["request_budget"],
                        run["concurrency"],
                        run["max_retries"],
                        run["started_at"],
                        run["database_bytes_before"],
                        run.get("report_path"),
                        _dump(run["request"]),
                    ],
                )
                _frame_insert(
                    connection,
                    table="historical_backfill_shards",
                    columns=[
                        "shard_id",
                        "run_id",
                        "shard_index",
                        "start_symbol",
                        "end_symbol",
                        "symbol_count",
                        "requested_date_range_json",
                        "status",
                        "resume_cursor",
                        "request_count",
                        "successful_symbol_count",
                        "skipped_symbol_count",
                        "failed_symbol_count",
                        "write_elapsed_seconds",
                        "started_at",
                        "completed_at",
                    ],
                    rows=[
                        [
                            shard["shard_id"],
                            run["run_id"],
                            shard["shard_index"],
                            shard["start_symbol"],
                            shard["end_symbol"],
                            shard["symbol_count"],
                            _dump(run["requested_date_range"]),
                            "PENDING",
                            0,
                            0,
                            0,
                            shard["initial_skipped_count"],
                            0,
                            None,
                            None,
                            None,
                        ]
                        for shard in shards
                    ],
                )
                _frame_insert(
                    connection,
                    table="historical_backfill_items",
                    columns=[
                        "run_id",
                        "shard_id",
                        "item_index",
                        "symbol",
                        "list_date",
                        "eligibility_status",
                        "expected_trading_days",
                        "observed_trading_days",
                        "provider_used",
                        "adjustment_type",
                        "status",
                        "attempt_count",
                        "request_count",
                        "raw_record_count",
                        "canonical_record_count",
                        "database_growth_bytes",
                        "fetch_elapsed_seconds",
                        "write_elapsed_seconds",
                        "started_at",
                        "completed_at",
                        "error_type",
                        "error_message",
                        "payload_json",
                    ],
                    rows=[
                        [
                            run["run_id"],
                            item["shard_id"],
                            item["item_index"],
                            item["symbol"],
                            item["list_date"],
                            item["eligibility_status"],
                            item["expected_trading_days"],
                            0,
                            None,
                            run["adjustment_type"],
                            item["status"],
                            0,
                            0,
                            0,
                            0,
                            None,
                            0.0,
                            0.0,
                            None,
                            (
                                run["started_at"]
                                if item["status"].startswith("SKIPPED")
                                else None
                            ),
                            None,
                            item.get("error_message"),
                            _dump(item.get("payload", {})),
                        ]
                        for item in items
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def active_universe_context(
        self,
        *,
        data_cutoff: datetime,
    ) -> dict[str, dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                WITH latest_version AS (
                    SELECT universe_version
                    FROM stock_universe_versions
                    WHERE data_cutoff <= ?
                    ORDER BY effective_at DESC
                    LIMIT 1
                )
                SELECT symbol, exchange, board, list_date, delist_date,
                       listing_status, is_suspended, universe_version
                FROM stock_universe
                WHERE universe_version = (
                    SELECT universe_version FROM latest_version
                )
                ORDER BY symbol
                """,
                [data_cutoff],
            ).fetchall()
        return {
            str(row[0]): {
                "exchange": str(row[1]),
                "board": str(row[2]),
                "list_date": row[3],
                "delist_date": row[4],
                "listing_status": str(row[5]),
                "is_suspended": bool(row[6]),
                "universe_version": str(row[7]),
            }
            for row in rows
        }

    def create_trade_date_run(
        self,
        *,
        run: dict[str, Any],
        trade_dates: list[date],
        expected_universe_count: int,
        force_fetch: bool,
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                prior: dict[date, dict[str, Any]] = {}
                if not force_fetch and trade_dates:
                    cursor = connection.execute(
                        """
                        SELECT trade_date, expected_universe_count,
                               returned_record_count, valid_record_count,
                               invalid_record_count, missing_symbol_count,
                               extra_symbol_count, beijing_record_count,
                               shanghai_record_count, shenzhen_record_count,
                               coverage_ratio, status, payload_json
                        FROM (
                            SELECT *,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY trade_date
                                       ORDER BY completed_at DESC, shard_id DESC
                                   ) AS recency
                            FROM historical_trade_date_shards
                            WHERE provider = ?
                              AND adjustment_type = ?
                              AND trade_date IN (
                                  SELECT UNNEST(?::DATE[])
                              )
                              AND status = 'SUCCESS'
                        )
                        WHERE recency = 1
                        """,
                        [
                            run["provider"],
                            run["adjustment_type"],
                            trade_dates,
                        ],
                    )
                    columns = [item[0] for item in cursor.description]
                    prior = {
                        row[0]: dict(zip(columns, row, strict=True))
                        for row in cursor.fetchall()
                    }
                now = run["started_at"]
                connection.execute(
                    """
                    INSERT INTO historical_backfill_runs (
                        run_id, mode, provider, fallback_providers_json,
                        selection_strategy, requested_symbols_json,
                        requested_date_range_json, target_trading_days,
                        adjustment_type, eligible_symbol_count,
                        completed_symbol_count, successful_symbol_count,
                        skipped_symbol_count, failed_symbol_count,
                        request_count, request_budget, concurrency,
                        max_retries, retry_count, started_at, completed_at,
                        status, resume_cursor, database_bytes_before,
                        database_bytes_after, persisted_raw_count,
                        persisted_canonical_count, report_path, request_json,
                        error_summary_json
                    )
                    VALUES (
                        ?, 'APPLY', ?, '[]', 'TRADE_DATE', '[]', ?, ?, ?,
                        ?, 0, 0, 0, 0, 0, ?, 1, ?, 0, ?, NULL, 'PENDING',
                        0, ?, NULL, 0, 0, ?, ?, '{}'
                    )
                    """,
                    [
                        run["run_id"],
                        run["provider"],
                        _dump(run["requested_date_range"]),
                        len(trade_dates),
                        run["adjustment_type"],
                        expected_universe_count,
                        run["request_budget"],
                        run["max_retries"],
                        now,
                        run["database_bytes_before"],
                        run.get("report_path"),
                        _dump(run["request"]),
                    ],
                )
                rows: list[list[Any]] = []
                for index, trade_date in enumerate(trade_dates):
                    previous = prior.get(trade_date)
                    skipped = previous is not None
                    previous_payload = (
                        _load(previous["payload_json"]) if previous else {}
                    )
                    payload = {
                        **previous_payload,
                        "skipped_existing_success": skipped,
                        "force_fetch": force_fetch,
                    }
                    rows.append(
                        [
                            f"{run['run_id']}_date_{trade_date:%Y%m%d}",
                            run["run_id"],
                            index,
                            "TRADE_DATE_SHARD",
                            run["provider"],
                            trade_date,
                            run["adjustment_type"],
                            (
                                int(previous["expected_universe_count"])
                                if previous
                                else expected_universe_count
                            ),
                            (
                                int(previous["returned_record_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["valid_record_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["invalid_record_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["missing_symbol_count"])
                                if previous
                                else expected_universe_count
                            ),
                            (
                                int(previous["extra_symbol_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["beijing_record_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["shanghai_record_count"])
                                if previous
                                else 0
                            ),
                            (
                                int(previous["shenzhen_record_count"])
                                if previous
                                else 0
                            ),
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0.0,
                            0,
                            (
                                float(previous["coverage_ratio"])
                                if previous
                                else 0.0
                            ),
                            str(previous["status"]) if previous else "PENDING",
                            now if skipped else None,
                            now if skipped else None,
                            None,
                            None,
                            _dump(payload),
                        ]
                    )
                _frame_insert(
                    connection,
                    table="historical_trade_date_shards",
                    columns=[
                        "shard_id",
                        "run_id",
                        "shard_index",
                        "shard_type",
                        "provider",
                        "trade_date",
                        "adjustment_type",
                        "expected_universe_count",
                        "returned_record_count",
                        "valid_record_count",
                        "invalid_record_count",
                        "missing_symbol_count",
                        "extra_symbol_count",
                        "beijing_record_count",
                        "shanghai_record_count",
                        "shenzhen_record_count",
                        "raw_inserted_count",
                        "canonical_inserted_count",
                        "duplicate_count",
                        "conflict_count",
                        "request_count",
                        "retry_count",
                        "latency_ms",
                        "write_elapsed_seconds",
                        "database_growth_bytes",
                        "coverage_ratio",
                        "status",
                        "started_at",
                        "completed_at",
                        "error_code",
                        "sanitized_error",
                        "payload_json",
                    ],
                    rows=rows,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def successful_trade_date_shards(
        self,
        *,
        provider: str,
        adjustment_type: str,
        trade_dates: list[date],
    ) -> dict[date, dict[str, Any]]:
        if not trade_dates:
            return {}
        with get_connection() as connection:
            cursor = connection.execute(
                """
                SELECT * EXCLUDE (recency)
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY trade_date
                               ORDER BY completed_at DESC, shard_id DESC
                           ) AS recency
                    FROM historical_trade_date_shards
                    WHERE provider = ?
                      AND adjustment_type = ?
                      AND trade_date IN (
                          SELECT UNNEST(?::DATE[])
                      )
                      AND status = 'SUCCESS'
                )
                WHERE recency = 1
                """,
                [provider, adjustment_type, trade_dates],
            )
            columns = [item[0] for item in cursor.description]
            rows = [
                dict(zip(columns, row, strict=True))
                for row in cursor.fetchall()
            ]
        for row in rows:
            row["payload_json"] = _load(row["payload_json"])
        return {row["trade_date"]: row for row in rows}

    def trade_date_shards(
        self,
        run_id: str,
        *,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [run_id]
        status_sql = ""
        if statuses:
            status_sql = " AND status IN (SELECT UNNEST(?::VARCHAR[]))"
            parameters.append(statuses)
        with get_connection() as connection:
            cursor = connection.execute(
                f"""
                SELECT * FROM historical_trade_date_shards
                WHERE run_id = ? {status_sql}
                ORDER BY shard_index
                """,
                parameters,
            )
            columns = [item[0] for item in cursor.description]
            rows = [
                dict(zip(columns, row, strict=True))
                for row in cursor.fetchall()
            ]
        for row in rows:
            row["payload_json"] = _load(row["payload_json"])
        return rows

    def mark_trade_date_run_running(
        self,
        run_id: str,
        *,
        started_at: datetime,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE historical_backfill_runs
                SET status = 'RUNNING', started_at = ?
                WHERE run_id = ? AND status IN (
                    'PENDING', 'PARTIAL', 'PAUSED_BUDGET',
                    'PAUSED_RATE_LIMIT', 'FAILED'
                )
                """,
                [started_at, run_id],
            )

    def persist_trade_date_batch(
        self,
        *,
        run_id: str,
        shard_id: str,
        raw_records: list[MarketRecord],
        historical_bars: list[dict[str, Any]],
        request_audits: list[dict[str, Any]],
        shard_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        started = perf_counter()
        database_bytes_before = self.database_bytes()
        raw_ids = [item.record_id for item in raw_records]
        symbols = sorted({item["symbol"] for item in historical_bars})
        trade_date = shard_metrics["trade_date"]
        adjustment_type = shard_metrics["adjustment_type"]
        with get_connection() as connection:
            existing_rows = connection.execute(
                """
                SELECT bar_id, symbol, open, high, low, close, volume,
                       amount, primary_source, source_record_ids_json,
                       verification_source_ids_json, verification_status,
                       content_hash, data_available_time, data_cutoff
                FROM canonical_historical_bars
                WHERE trade_date = ? AND adjustment_type = ?
                  AND symbol IN (SELECT UNNEST(?::VARCHAR[]))
                """,
                [trade_date, adjustment_type, symbols],
            ).fetchall() if symbols else []
            existing_by_symbol: dict[str, list[dict[str, Any]]] = {}
            for row in existing_rows:
                value = {
                    "bar_id": row[0],
                    "symbol": row[1],
                    "open": row[2],
                    "high": row[3],
                    "low": row[4],
                    "close": row[5],
                    "volume": row[6],
                    "amount": row[7],
                    "primary_source": row[8],
                    "source_record_ids": _load(row[9]),
                    "verification_source_ids": _load(row[10]),
                    "verification_status": row[11],
                    "content_hash": row[12],
                    "data_available_time": row[13],
                    "data_cutoff": row[14],
                }
                existing_by_symbol.setdefault(str(row[1]), []).append(value)
            inserts: list[dict[str, Any]] = []
            evidence_updates: list[list[Any]] = []
            conflict_symbols: set[str] = set()
            duplicate_count = 0
            for bar in historical_bars:
                candidates = existing_by_symbol.get(bar["symbol"], [])
                matched = next(
                    (
                        candidate
                        for candidate in candidates
                        if _historical_values_match(candidate, bar)
                    ),
                    None,
                )
                if matched is None:
                    inserts.append(bar)
                    if candidates:
                        conflict_symbols.add(bar["symbol"])
                    continue
                duplicate_count += 1
                sources = sorted(
                    {
                        *matched["source_record_ids"],
                        *bar["source_record_ids"],
                    }
                )
                verification = sorted(
                    set(matched["verification_source_ids"])
                )
                status = matched["verification_status"]
                confidence = 0.5
                if status == "CONFLICT":
                    confidence = 0.0
                    conflict_symbols.add(bar["symbol"])
                elif matched["primary_source"] != bar["primary_source"]:
                    status = "VERIFIED"
                    confidence = 1.0
                    verification = sorted(
                        {
                            *verification,
                            *bar["source_record_ids"],
                        }
                    )
                evidence_updates.append(
                    [
                        matched["bar_id"],
                        _dump(sources),
                        _dump(verification),
                        status,
                        confidence,
                        min(
                            matched["data_available_time"],
                            bar["data_available_time"],
                        ),
                        min(matched["data_cutoff"], bar["data_cutoff"]),
                    ]
                )
            raw_before = (
                int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM data_records
                        WHERE record_id IN (
                            SELECT UNNEST(?::VARCHAR[])
                        )
                        """,
                        [raw_ids],
                    ).fetchone()[0]
                )
                if raw_ids
                else 0
            )
            insert_ids = [item["bar_id"] for item in inserts]
            bars_before = (
                int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM canonical_historical_bars
                        WHERE bar_id IN (
                            SELECT UNNEST(?::VARCHAR[])
                        )
                        """,
                        [insert_ids],
                    ).fetchone()[0]
                )
                if insert_ids
                else 0
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                _frame_insert(
                    connection,
                    table="data_records",
                    columns=[
                        "record_id",
                        "task_id",
                        "symbol",
                        "data_type",
                        "event_time",
                        "fetched_at",
                        "source_name",
                        "source_url",
                        "source_level",
                        "verified",
                        "content_hash",
                        "payload_json",
                    ],
                    rows=[
                        [
                            item.record_id,
                            None,
                            item.symbol,
                            item.data_type.value,
                            item.event_time,
                            item.fetched_at,
                            item.source_name,
                            (
                                str(item.source_url)
                                if item.source_url
                                else None
                            ),
                            item.source_level.value,
                            item.verified,
                            item.content_hash,
                            _dump(item.data),
                        ]
                        for item in raw_records
                    ],
                )
                _frame_insert(
                    connection,
                    table="canonical_historical_bars",
                    columns=[
                        "bar_id",
                        "symbol",
                        "trade_date",
                        "event_time",
                        "data_available_time",
                        "data_cutoff",
                        "generated_at",
                        "adjustment_type",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "amount",
                        "volume_unit",
                        "amount_unit",
                        "primary_source",
                        "source_record_ids_json",
                        "verification_source_ids_json",
                        "verification_status",
                        "confidence",
                        "content_hash",
                        "algorithm_version",
                        "raw_payload_json",
                    ],
                    rows=[
                        [
                            item["bar_id"],
                            item["symbol"],
                            item["trade_date"],
                            item["event_time"],
                            item["data_available_time"],
                            item["data_cutoff"],
                            item["generated_at"],
                            item["adjustment_type"],
                            item["open"],
                            item["high"],
                            item["low"],
                            item["close"],
                            item["volume"],
                            item["amount"],
                            item["volume_unit"],
                            item["amount_unit"],
                            item["primary_source"],
                            _dump(item["source_record_ids"]),
                            _dump(item["verification_source_ids"]),
                            item["verification_status"],
                            item["confidence"],
                            item["content_hash"],
                            item["algorithm_version"],
                            _dump(item["raw_payload"]),
                        ]
                        for item in inserts
                    ],
                )
                if evidence_updates:
                    view = "_history_evidence_updates"
                    columns = [
                        "bar_id",
                        "source_record_ids_json",
                        "verification_source_ids_json",
                        "verification_status",
                        "confidence",
                        "data_available_time",
                        "data_cutoff",
                    ]
                    connection.register(
                        view,
                        pd.DataFrame.from_records(
                            evidence_updates,
                            columns=columns,
                        ),
                    )
                    try:
                        connection.execute(
                            f"""
                            UPDATE canonical_historical_bars AS target
                            SET source_record_ids_json =
                                    source.source_record_ids_json,
                                verification_source_ids_json =
                                    source.verification_source_ids_json,
                                verification_status =
                                    source.verification_status,
                                confidence = source.confidence,
                                data_available_time =
                                    source.data_available_time,
                                data_cutoff = source.data_cutoff
                            FROM "{view}" AS source
                            WHERE target.bar_id = source.bar_id
                            """
                        )
                    finally:
                        connection.unregister(view)
                if conflict_symbols:
                    connection.execute(
                        """
                        UPDATE canonical_historical_bars
                        SET verification_status = 'CONFLICT', confidence = 0
                        WHERE trade_date = ? AND adjustment_type = ?
                          AND symbol IN (
                              SELECT UNNEST(?::VARCHAR[])
                          )
                        """,
                        [
                            trade_date,
                            adjustment_type,
                            sorted(conflict_symbols),
                        ],
                    )
                _frame_insert(
                    connection,
                    table="provider_request_audits",
                    columns=[
                        "audit_id",
                        "run_id",
                        "shard_id",
                        "symbol",
                        "provider",
                        "capability",
                        "attempt",
                        "request_started_at",
                        "request_completed_at",
                        "status",
                        "record_count",
                        "latency_ms",
                        "rate_limited",
                        "error_type",
                        "error_message",
                        "request_hash",
                        "request_parameters_json",
                    ],
                    rows=[
                        [
                            request_audit["audit_id"],
                            run_id,
                            shard_id,
                            None,
                            request_audit["provider"],
                            request_audit["capability"],
                            request_audit["attempt"],
                            request_audit["request_started_at"],
                            request_audit["request_completed_at"],
                            request_audit["status"],
                            request_audit["record_count"],
                            request_audit["latency_ms"],
                            request_audit["rate_limited"],
                            request_audit.get("error_type"),
                            request_audit.get("error_message"),
                            request_audit["request_hash"],
                            _dump(request_audit["request_parameters"]),
                        ]
                        for request_audit in request_audits
                    ],
                )
                raw_inserted = max(0, len(set(raw_ids)) - raw_before)
                canonical_inserted = max(
                    0,
                    len(set(insert_ids)) - bars_before,
                )
                conflict_count = (
                    int(
                        connection.execute(
                            """
                            SELECT COUNT(DISTINCT symbol)
                            FROM canonical_historical_bars
                            WHERE trade_date = ? AND adjustment_type = ?
                              AND verification_status = 'CONFLICT'
                              AND symbol IN (
                                  SELECT UNNEST(?::VARCHAR[])
                              )
                            """,
                            [trade_date, adjustment_type, symbols],
                        ).fetchone()[0]
                    )
                    if symbols
                    else 0
                )
                final_status = shard_metrics["status"]
                if (
                    final_status == "SUCCESS"
                    and shard_metrics["valid_record_count"] > 0
                    and conflict_count
                    / shard_metrics["valid_record_count"]
                    > 0.005
                ):
                    final_status = "SUCCESS_PARTIAL"
                write_elapsed = max(0.0, perf_counter() - started)
                connection.execute(
                    """
                    UPDATE historical_trade_date_shards
                    SET expected_universe_count = ?,
                        returned_record_count = ?,
                        valid_record_count = ?,
                        invalid_record_count = ?,
                        missing_symbol_count = ?,
                        extra_symbol_count = ?,
                        beijing_record_count = ?,
                        shanghai_record_count = ?,
                        shenzhen_record_count = ?,
                        raw_inserted_count = ?,
                        canonical_inserted_count = ?,
                        duplicate_count = ?,
                        conflict_count = ?,
                        request_count = request_count + ?,
                        retry_count = retry_count + ?,
                        latency_ms = latency_ms + ?,
                        write_elapsed_seconds = ?,
                        coverage_ratio = ?,
                        status = ?,
                        started_at = COALESCE(started_at, ?),
                        completed_at = ?,
                        error_code = NULL,
                        sanitized_error = NULL,
                        payload_json = ?
                    WHERE run_id = ? AND shard_id = ?
                    """,
                    [
                        shard_metrics["expected_universe_count"],
                        shard_metrics["returned_record_count"],
                        shard_metrics["valid_record_count"],
                        shard_metrics["invalid_record_count"],
                        shard_metrics["missing_symbol_count"],
                        shard_metrics["extra_symbol_count"],
                        shard_metrics["beijing_record_count"],
                        shard_metrics["shanghai_record_count"],
                        shard_metrics["shenzhen_record_count"],
                        raw_inserted,
                        canonical_inserted,
                        duplicate_count,
                        conflict_count,
                        shard_metrics["request_count"],
                        shard_metrics["retry_count"],
                        shard_metrics["latency_ms"],
                        write_elapsed,
                        shard_metrics["coverage_ratio"],
                        final_status,
                        shard_metrics["started_at"],
                        shard_metrics["completed_at"],
                        _dump(shard_metrics.get("payload", {})),
                        run_id,
                        shard_id,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        growth = max(0, self.database_bytes() - database_bytes_before)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE historical_trade_date_shards
                SET database_growth_bytes = ?
                WHERE run_id = ? AND shard_id = ?
                """,
                [growth, run_id, shard_id],
            )
        return {
            "raw_inserted_count": raw_inserted,
            "canonical_inserted_count": canonical_inserted,
            "duplicate_count": duplicate_count,
            "conflict_count": conflict_count,
            "status": final_status,
            "write_elapsed_seconds": write_elapsed,
            "database_growth_bytes": growth,
        }

    def record_trade_date_failure(
        self,
        *,
        run_id: str,
        shard_id: str,
        status: str,
        request_audits: list[dict[str, Any]],
        request_count: int,
        retry_count: int,
        latency_ms: int,
        started_at: datetime,
        completed_at: datetime,
        error_code: str,
        sanitized_error: str,
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                _frame_insert(
                    connection,
                    table="provider_request_audits",
                    columns=[
                        "audit_id",
                        "run_id",
                        "shard_id",
                        "symbol",
                        "provider",
                        "capability",
                        "attempt",
                        "request_started_at",
                        "request_completed_at",
                        "status",
                        "record_count",
                        "latency_ms",
                        "rate_limited",
                        "error_type",
                        "error_message",
                        "request_hash",
                        "request_parameters_json",
                    ],
                    rows=[
                        [
                            item["audit_id"],
                            run_id,
                            shard_id,
                            None,
                            item["provider"],
                            item["capability"],
                            item["attempt"],
                            item["request_started_at"],
                            item["request_completed_at"],
                            item["status"],
                            item["record_count"],
                            item["latency_ms"],
                            item["rate_limited"],
                            item.get("error_type"),
                            item.get("error_message"),
                            item["request_hash"],
                            _dump(item["request_parameters"]),
                        ]
                        for item in request_audits
                    ],
                )
                connection.execute(
                    """
                    UPDATE historical_trade_date_shards
                    SET status = ?, request_count = request_count + ?,
                        retry_count = retry_count + ?,
                        latency_ms = latency_ms + ?,
                        started_at = COALESCE(started_at, ?),
                        completed_at = ?, error_code = ?,
                        sanitized_error = ?
                    WHERE run_id = ? AND shard_id = ?
                    """,
                    [
                        status,
                        request_count,
                        retry_count,
                        latency_ms,
                        started_at,
                        completed_at,
                        error_code,
                        sanitized_error[:500],
                        run_id,
                        shard_id,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def finalize_trade_date_run(
        self,
        run_id: str,
        *,
        completed_at: datetime,
        database_bytes_after: int,
        status: str,
        errors: list[dict[str, Any]],
    ) -> None:
        with get_connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status != 'PENDING'),
                    COUNT(*) FILTER (
                        WHERE status IN (
                            'SUCCESS', 'SUCCESS_PARTIAL', 'SUCCESS_EMPTY'
                        )
                    ),
                    COUNT(*) FILTER (WHERE status = 'FAILED'),
                    COALESCE(SUM(request_count), 0),
                    COALESCE(SUM(retry_count), 0),
                    COALESCE(SUM(raw_inserted_count), 0),
                    COALESCE(SUM(canonical_inserted_count), 0),
                    COALESCE(
                        MIN(shard_index) FILTER (
                            WHERE status IN (
                                'PENDING', 'FAILED', 'PAUSED_BUDGET',
                                'PAUSED_RATE_LIMIT'
                            )
                        ),
                        COUNT(*)
                    )
                FROM historical_trade_date_shards
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            connection.execute(
                """
                UPDATE historical_backfill_runs
                SET completed_symbol_count = ?,
                    successful_symbol_count = ?,
                    skipped_symbol_count = 0,
                    failed_symbol_count = ?,
                    request_count = ?,
                    retry_count = ?,
                    completed_at = ?,
                    status = ?,
                    resume_cursor = ?,
                    database_bytes_after = ?,
                    persisted_raw_count = ?,
                    persisted_canonical_count = ?,
                    error_summary_json = ?
                WHERE run_id = ?
                """,
                [
                    counts[0],
                    counts[1],
                    counts[2],
                    counts[3],
                    counts[4],
                    completed_at,
                    status,
                    counts[7],
                    database_bytes_after,
                    counts[5],
                    counts[6],
                    _dump(errors),
                    run_id,
                ],
            )

    def pending_items(
        self,
        run_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, shard_id, item_index, symbol, list_date,
                       eligibility_status, expected_trading_days,
                       adjustment_type
                FROM historical_backfill_items
                WHERE run_id = ? AND status = 'PENDING'
                ORDER BY item_index
                LIMIT ?
                """,
                [run_id, limit],
            ).fetchall()
        return [
            {
                "run_id": row[0],
                "shard_id": row[1],
                "item_index": int(row[2]),
                "symbol": row[3],
                "list_date": row[4],
                "eligibility_status": row[5],
                "expected_trading_days": int(row[6]),
                "adjustment_type": row[7],
            }
            for row in rows
        ]

    def mark_running(
        self,
        run_id: str,
        *,
        started_at: datetime,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE historical_backfill_runs
                SET status = 'RUNNING', started_at = ?
                WHERE run_id = ? AND status IN (
                    'PENDING', 'PARTIAL', 'PAUSED_BUDGET',
                    'PAUSED_RATE_LIMIT'
                )
                """,
                [started_at, run_id],
            )

    def persist_batch(
        self,
        *,
        raw_records: list[MarketRecord],
        historical_bars: list[dict[str, Any]],
        item_updates: list[dict[str, Any]],
        request_audits: list[dict[str, Any]],
    ) -> tuple[int, int, float]:
        started = perf_counter()
        database_bytes_before = self.database_bytes()
        raw_ids = [item.record_id for item in raw_records]
        bar_ids = [item["bar_id"] for item in historical_bars]
        with get_connection() as connection:
            raw_before = (
                int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM data_records
                        WHERE record_id IN (
                            SELECT UNNEST(?::VARCHAR[])
                        )
                        """,
                        [raw_ids],
                    ).fetchone()[0]
                )
                if raw_ids
                else 0
            )
            bars_before = (
                int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM canonical_historical_bars
                        WHERE bar_id IN (
                            SELECT UNNEST(?::VARCHAR[])
                        )
                        """,
                        [bar_ids],
                    ).fetchone()[0]
                )
                if bar_ids
                else 0
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                _frame_insert(
                    connection,
                    table="data_records",
                    columns=[
                        "record_id",
                        "task_id",
                        "symbol",
                        "data_type",
                        "event_time",
                        "fetched_at",
                        "source_name",
                        "source_url",
                        "source_level",
                        "verified",
                        "content_hash",
                        "payload_json",
                    ],
                    rows=[
                        [
                            item.record_id,
                            None,
                            item.symbol,
                            item.data_type.value,
                            item.event_time,
                            item.fetched_at,
                            item.source_name,
                            str(item.source_url) if item.source_url else None,
                            item.source_level.value,
                            item.verified,
                            item.content_hash,
                            _dump(item.data),
                        ]
                        for item in raw_records
                    ],
                )
                _frame_insert(
                    connection,
                    table="canonical_historical_bars",
                    columns=[
                        "bar_id",
                        "symbol",
                        "trade_date",
                        "event_time",
                        "data_available_time",
                        "data_cutoff",
                        "generated_at",
                        "adjustment_type",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "amount",
                        "volume_unit",
                        "amount_unit",
                        "primary_source",
                        "source_record_ids_json",
                        "verification_source_ids_json",
                        "verification_status",
                        "confidence",
                        "content_hash",
                        "algorithm_version",
                        "raw_payload_json",
                    ],
                    rows=[
                        [
                            item["bar_id"],
                            item["symbol"],
                            item["trade_date"],
                            item["event_time"],
                            item["data_available_time"],
                            item["data_cutoff"],
                            item["generated_at"],
                            item["adjustment_type"],
                            item["open"],
                            item["high"],
                            item["low"],
                            item["close"],
                            item["volume"],
                            item["amount"],
                            item["volume_unit"],
                            item["amount_unit"],
                            item["primary_source"],
                            _dump(item["source_record_ids"]),
                            _dump(item["verification_source_ids"]),
                            item["verification_status"],
                            item["confidence"],
                            item["content_hash"],
                            item["algorithm_version"],
                            _dump(item["raw_payload"]),
                        ]
                        for item in historical_bars
                    ],
                )
                if historical_bars:
                    affected_symbols = sorted(
                        {item["symbol"] for item in historical_bars}
                    )
                    connection.execute(
                        """
                        UPDATE canonical_historical_bars AS target
                        SET verification_status = 'CONFLICT',
                            confidence = 0
                        FROM (
                            SELECT symbol, trade_date, adjustment_type
                            FROM canonical_historical_bars
                            WHERE symbol IN (
                                SELECT UNNEST(?::VARCHAR[])
                            )
                            GROUP BY symbol, trade_date, adjustment_type
                            HAVING COUNT(DISTINCT content_hash) > 1
                        ) AS conflicts
                        WHERE target.symbol = conflicts.symbol
                          AND target.trade_date = conflicts.trade_date
                          AND target.adjustment_type =
                              conflicts.adjustment_type
                        """,
                        [affected_symbols],
                    )
                _frame_insert(
                    connection,
                    table="provider_request_audits",
                    columns=[
                        "audit_id",
                        "run_id",
                        "shard_id",
                        "symbol",
                        "provider",
                        "capability",
                        "attempt",
                        "request_started_at",
                        "request_completed_at",
                        "status",
                        "record_count",
                        "latency_ms",
                        "rate_limited",
                        "error_type",
                        "error_message",
                        "request_hash",
                        "request_parameters_json",
                    ],
                    rows=[
                        [
                            item["audit_id"],
                            item.get("run_id"),
                            item.get("shard_id"),
                            item.get("symbol"),
                            item["provider"],
                            item["capability"],
                            item["attempt"],
                            item["request_started_at"],
                            item["request_completed_at"],
                            item["status"],
                            item["record_count"],
                            item["latency_ms"],
                            item["rate_limited"],
                            item.get("error_type"),
                            item.get("error_message"),
                            item["request_hash"],
                            _dump(item["request_parameters"]),
                        ]
                        for item in request_audits
                    ],
                )
                if item_updates:
                    view = "_history_item_updates"
                    columns = [
                        "run_id",
                        "symbol",
                        "observed_trading_days",
                        "provider_used",
                        "status",
                        "attempt_count",
                        "request_count",
                        "raw_record_count",
                        "canonical_record_count",
                        "fetch_elapsed_seconds",
                        "write_elapsed_seconds",
                        "started_at",
                        "completed_at",
                        "error_type",
                        "error_message",
                        "payload_json",
                    ]
                    connection.register(
                        view,
                        pd.DataFrame.from_records(
                            [
                                [
                                    item[column]
                                    if column != "payload_json"
                                    else _dump(item.get("payload", {}))
                                    for column in columns
                                ]
                                for item in item_updates
                            ],
                            columns=columns,
                        ),
                    )
                    try:
                        connection.execute(
                            f"""
                            UPDATE historical_backfill_items AS target
                            SET observed_trading_days =
                                    source.observed_trading_days,
                                provider_used = source.provider_used,
                                status = source.status,
                                attempt_count = source.attempt_count,
                                request_count = source.request_count,
                                raw_record_count = source.raw_record_count,
                                canonical_record_count =
                                    source.canonical_record_count,
                                fetch_elapsed_seconds =
                                    source.fetch_elapsed_seconds,
                                write_elapsed_seconds =
                                    source.write_elapsed_seconds,
                                started_at = source.started_at,
                                completed_at = source.completed_at,
                                error_type = source.error_type,
                                error_message = source.error_message,
                                payload_json = source.payload_json
                            FROM "{view}" AS source
                            WHERE target.run_id = source.run_id
                              AND target.symbol = source.symbol
                            """
                        )
                    finally:
                        connection.unregister(view)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        raw_inserted = max(0, len(set(raw_ids)) - raw_before)
        bars_inserted = max(0, len(set(bar_ids)) - bars_before)
        write_elapsed = max(0.0, perf_counter() - started)
        database_growth = max(
            0,
            self.database_bytes() - database_bytes_before,
        )
        if item_updates:
            symbols = [item["symbol"] for item in item_updates]
            per_item_growth = database_growth // len(symbols)
            with get_connection() as connection:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        """
                        UPDATE historical_backfill_items
                        SET write_elapsed_seconds = ?,
                            database_growth_bytes = ?
                        WHERE run_id = ?
                          AND symbol IN (
                            SELECT UNNEST(?::VARCHAR[])
                          )
                        """,
                        [
                            write_elapsed,
                            per_item_growth,
                            item_updates[0]["run_id"],
                            symbols,
                        ],
                    )
                    connection.execute(
                        """
                        UPDATE historical_backfill_shards AS shard
                        SET request_count = aggregate.request_count,
                            successful_symbol_count =
                                aggregate.success_count,
                            skipped_symbol_count = aggregate.skipped_count,
                            failed_symbol_count = aggregate.failed_count,
                            resume_cursor = aggregate.resume_cursor,
                            write_elapsed_seconds =
                                aggregate.write_elapsed_seconds,
                            started_at = COALESCE(
                                shard.started_at,
                                aggregate.started_at
                            ),
                            completed_at = aggregate.completed_at,
                            status = CASE
                                WHEN aggregate.pending_count > 0
                                    THEN 'PARTIAL'
                                WHEN aggregate.failed_count > 0
                                    THEN 'PARTIAL'
                                ELSE 'SUCCESS'
                            END
                        FROM (
                            SELECT
                                shard_id,
                                COALESCE(SUM(request_count), 0)
                                    AS request_count,
                                COUNT(*) FILTER (
                                    WHERE status LIKE 'SUCCESS%'
                                ) AS success_count,
                                COUNT(*) FILTER (
                                    WHERE status LIKE 'SKIPPED%'
                                ) AS skipped_count,
                                COUNT(*) FILTER (
                                    WHERE status = 'FAILED'
                                ) AS failed_count,
                                COUNT(*) FILTER (
                                    WHERE status = 'PENDING'
                                ) AS pending_count,
                                COALESCE(MAX(item_index) FILTER (
                                    WHERE status != 'PENDING'
                                ), -1) + 1 AS resume_cursor,
                                COALESCE(
                                    SUM(write_elapsed_seconds),
                                    0
                                ) AS write_elapsed_seconds,
                                MIN(started_at) AS started_at,
                                MAX(completed_at) AS completed_at
                            FROM historical_backfill_items
                            WHERE run_id = ?
                            GROUP BY shard_id
                        ) AS aggregate
                        WHERE shard.shard_id = aggregate.shard_id
                        """,
                        [item_updates[0]["run_id"]],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
        return raw_inserted, bars_inserted, write_elapsed

    def mark_items_failed(
        self,
        *,
        run_id: str,
        symbols: list[str],
        completed_at: datetime,
        error_type: str,
        error_message: str,
    ) -> None:
        if not symbols:
            return
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    UPDATE historical_backfill_items
                    SET status = 'FAILED',
                        completed_at = ?,
                        error_type = ?,
                        error_message = ?
                    WHERE run_id = ?
                      AND symbol IN (
                        SELECT UNNEST(?::VARCHAR[])
                      )
                    """,
                    [
                        completed_at,
                        error_type,
                        error_message[:500],
                        run_id,
                        symbols,
                    ],
                )
                connection.execute(
                    """
                    UPDATE historical_backfill_shards
                    SET status = 'FAILED',
                        failed_symbol_count = symbol_count,
                        completed_at = ?
                    WHERE run_id = ?
                      AND shard_id IN (
                        SELECT DISTINCT shard_id
                        FROM historical_backfill_items
                        WHERE run_id = ?
                          AND symbol IN (
                            SELECT UNNEST(?::VARCHAR[])
                          )
                      )
                    """,
                    [completed_at, run_id, run_id, symbols],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        completed_at: datetime,
        database_bytes_after: int,
        persisted_raw_count: int,
        persisted_canonical_count: int,
        error_summary: list[dict[str, Any]],
        extra_request_count: int = 0,
    ) -> None:
        with get_connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status NOT IN ('PENDING')
                    ),
                    COUNT(*) FILTER (
                        WHERE status LIKE 'SUCCESS%'
                    ),
                    COUNT(*) FILTER (
                        WHERE status LIKE 'SKIPPED%'
                    ),
                    COUNT(*) FILTER (
                        WHERE status = 'FAILED'
                    ),
                    COALESCE(SUM(request_count), 0),
                    (
                        SELECT COUNT(*)
                        FROM provider_request_audits a
                        WHERE a.run_id = ?
                          AND a.attempt > 1
                    ),
                    COALESCE(MAX(item_index) FILTER (
                        WHERE status NOT IN ('PENDING')
                    ), -1) + 1
                FROM historical_backfill_items
                WHERE run_id = ?
                """,
                [run_id, run_id],
            ).fetchone()
            connection.execute(
                """
                UPDATE historical_backfill_runs
                SET completed_symbol_count = ?,
                    successful_symbol_count = ?,
                    skipped_symbol_count = ?,
                    failed_symbol_count = ?,
                    request_count = ?,
                    retry_count = ?,
                    completed_at = ?,
                    status = ?,
                    resume_cursor = ?,
                    database_bytes_after = ?,
                    persisted_raw_count = persisted_raw_count + ?,
                    persisted_canonical_count =
                        persisted_canonical_count + ?,
                    error_summary_json = ?
                WHERE run_id = ?
                """,
                [
                    *counts[:4],
                    int(counts[4]) + extra_request_count,
                    counts[5],
                    completed_at,
                    status,
                    int(counts[6]),
                    database_bytes_after,
                    persisted_raw_count,
                    persisted_canonical_count,
                    _dump(error_summary),
                    run_id,
                ],
            )

    def save_daily_update(
        self,
        *,
        run: dict[str, Any],
        steps: list[dict[str, Any]],
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO daily_data_update_runs (
                        run_id, mode, data_cutoff, status,
                        request_budget, request_count, model_call_count,
                        started_at, completed_at, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    [
                        run["run_id"],
                        run["mode"],
                        run["data_cutoff"],
                        run["status"],
                        run["request_budget"],
                        run["request_count"],
                        run["model_call_count"],
                        run["started_at"],
                        run["completed_at"],
                        _dump(run),
                    ],
                )
                _frame_insert(
                    connection,
                    table="daily_data_update_steps",
                    columns=[
                        "run_id",
                        "step_index",
                        "step_name",
                        "status",
                        "processed_count",
                        "started_at",
                        "completed_at",
                        "payload_json",
                    ],
                    rows=[
                        [
                            run["run_id"],
                            index,
                            step["name"],
                            step["status"],
                            step.get("processed_count", 0),
                            step["started_at"],
                            step["completed_at"],
                            _dump(step),
                        ]
                        for index, step in enumerate(steps)
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def run_detail(self, run_id: str) -> dict[str, Any] | None:
        with get_connection() as connection:
            run_cursor = connection.execute(
                "SELECT * FROM historical_backfill_runs WHERE run_id = ?",
                [run_id],
            )
            run_row = run_cursor.fetchone()
            if run_row is None:
                return None
            run = dict(
                zip(
                    [item[0] for item in run_cursor.description],
                    run_row,
                    strict=True,
                )
            )
            shard_cursor = connection.execute(
                """
                SELECT * FROM historical_backfill_shards
                WHERE run_id = ? ORDER BY shard_index
                """,
                [run_id],
            )
            shard_columns = [item[0] for item in shard_cursor.description]
            shards = [
                dict(zip(shard_columns, row, strict=True))
                for row in shard_cursor.fetchall()
            ]
            item_cursor = connection.execute(
                """
                SELECT * FROM historical_backfill_items
                WHERE run_id = ? ORDER BY item_index
                """,
                [run_id],
            )
            item_columns = [item[0] for item in item_cursor.description]
            items = [
                dict(zip(item_columns, row, strict=True))
                for row in item_cursor.fetchall()
            ]
            audit_cursor = connection.execute(
                """
                SELECT * FROM provider_request_audits
                WHERE run_id = ?
                ORDER BY request_started_at, audit_id
                """,
                [run_id],
            )
            audit_columns = [item[0] for item in audit_cursor.description]
            audits = [
                dict(zip(audit_columns, row, strict=True))
                for row in audit_cursor.fetchall()
            ]
            trade_date_cursor = connection.execute(
                """
                SELECT * FROM historical_trade_date_shards
                WHERE run_id = ? ORDER BY shard_index
                """,
                [run_id],
            )
            trade_date_columns = [
                item[0] for item in trade_date_cursor.description
            ]
            date_shards = [
                dict(zip(trade_date_columns, row, strict=True))
                for row in trade_date_cursor.fetchall()
            ]
        for value in (run, *shards, *items, *audits, *date_shards):
            for key, item in list(value.items()):
                if key.endswith("_json"):
                    value[key] = _load(item)
        return {
            "run": run,
            "shards": shards,
            "items": items,
            "request_audits": audits,
            "date_shards": date_shards,
        }

    def cancel(self, run_id: str, completed_at: datetime) -> bool:
        with get_connection() as connection:
            row = connection.execute(
                """
                UPDATE historical_backfill_runs
                SET status = 'CANCELLED', completed_at = ?
                WHERE run_id = ? AND status NOT IN ('SUCCESS', 'CANCELLED')
                RETURNING run_id
                """,
                [completed_at, run_id],
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE historical_backfill_shards
                    SET status = 'CANCELLED', completed_at = ?
                    WHERE run_id = ? AND status NOT IN ('SUCCESS', 'CANCELLED')
                    """,
                    [completed_at, run_id],
                )
                connection.execute(
                    """
                    UPDATE historical_trade_date_shards
                    SET status = 'CANCELLED', completed_at = ?
                    WHERE run_id = ? AND status NOT IN (
                        'SUCCESS', 'SUCCESS_PARTIAL', 'SUCCESS_EMPTY',
                        'CANCELLED'
                    )
                    """,
                    [completed_at, run_id],
                )
                connection.execute(
                    """
                    UPDATE historical_backfill_items
                    SET status = 'SKIPPED_CANCELLED', completed_at = ?
                    WHERE run_id = ? AND status = 'PENDING'
                    """,
                    [completed_at, run_id],
                )
        return row is not None


__all__ = ["HistoryRepository"]
