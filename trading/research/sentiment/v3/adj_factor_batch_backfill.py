from __future__ import annotations

import hashlib
import time
from datetime import date, datetime
from typing import Any, Callable

from data_hub.providers.tushare_provider import classify_tushare_error
from data_hub.services.full_market_common import stable_hash
from trading.research.sentiment.v3.adj_factor_coverage import (
    BACKFILL_VERSION,
    SHANGHAI,
    AdjustmentFactorBackfill,
    AdjustmentFactorDateProvider,
    BackfillMetrics,
    _connection,
)


class BatchedAdjustmentFactorBackfill(AdjustmentFactorBackfill):
    """Date-checkpointed repair with batched DuckDB index maintenance."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        provider: AdjustmentFactorDateProvider,
        minimum_request_interval_seconds: float = 0.20,
        database_batch_dates: int = 20,
    ) -> None:
        super().__init__(
            connection_factory=connection_factory,
            provider=provider,
            minimum_request_interval_seconds=minimum_request_interval_seconds,
        )
        if database_batch_dates <= 0:
            raise ValueError("database_batch_dates must be positive")
        self.database_batch_dates = database_batch_dates

    def run(
        self,
        *,
        start_date: date,
        end_date: date,
        run_id: str | None = None,
        max_dates: int | None = None,
    ) -> BackfillMetrics:
        started = time.perf_counter()
        if start_date > end_date:
            raise ValueError("start_date cannot exceed end_date")
        run_id = run_id or (
            "s3a1_adj_" + hashlib.sha256(
                f"{BACKFILL_VERSION}|{self.provider.source}|{start_date}|{end_date}".encode()
            ).hexdigest()[:20]
        )
        now = datetime.now(tz=SHANGHAI)
        with _connection(self.connection_factory) as connection:
            self.ensure_metadata_tables(connection)
            eligible_dates = self._eligible_dates(connection, start_date, end_date)
            connection.execute(
                """
                INSERT INTO sentiment_v3_adj_factor_backfill_runs
                VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, 0, 0, 0, 0, 0, 0, ?, NULL, ?)
                ON CONFLICT (run_id) DO NOTHING
                """,
                [
                    run_id, BACKFILL_VERSION, self.provider.source,
                    start_date, end_date, len(eligible_dates), now, now,
                ],
            )
            for day, eligible, existing in eligible_dates:
                connection.execute(
                    """
                    INSERT INTO sentiment_v3_adj_factor_backfill_items
                    (run_id, trade_date, status, eligible_rows, existing_rows, updated_at)
                    VALUES (?, ?, 'PENDING', ?, ?, ?)
                    ON CONFLICT (run_id, trade_date) DO NOTHING
                    """,
                    [run_id, day, eligible, existing, now],
                )
            pending = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT trade_date
                    FROM sentiment_v3_adj_factor_backfill_items
                    WHERE run_id = ? AND status IN ('PENDING', 'FAILED')
                    ORDER BY trade_date
                    """,
                    [run_id],
                ).fetchall()
            ]
            if max_dates is not None:
                pending = pending[: max(0, max_dates)]

            api_calls = fetched_total = normalized_total = inserted_total = 0
            request_ms = normalization_ms = write_ms = 0.0
            last_request_finished = 0.0
            buffer: list[tuple[date, int, list[list[Any]], float, str]] = []

            def flush() -> None:
                nonlocal inserted_total, write_ms
                if not buffer:
                    return
                write_started = time.perf_counter()
                all_rows = [row for _, _, rows, _, _ in buffer for row in rows]
                connection.execute("BEGIN TRANSACTION")
                try:
                    inserted = self._insert_rows(connection, all_rows)
                    if inserted != len(all_rows):
                        raise RuntimeError("ADJ_FACTOR_BATCH_INSERT_COUNT_MISMATCH")
                    for day, fetched_count, rows, latency, digest in buffer:
                        connection.execute(
                            """
                            UPDATE sentiment_v3_adj_factor_backfill_items
                            SET status = 'SUCCESS', fetched_rows = ?,
                                normalized_rows = ?, inserted_rows = ?,
                                request_latency_ms = ?, error_type = NULL,
                                error_message_sanitized = NULL, content_hash = ?,
                                updated_at = ?
                            WHERE run_id = ? AND trade_date = ?
                            """,
                            [
                                fetched_count, len(rows), len(rows), latency,
                                digest, datetime.now(tz=SHANGHAI), run_id, day,
                            ],
                        )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                inserted_total += inserted
                write_ms += (time.perf_counter() - write_started) * 1000
                buffer.clear()

            for day in pending:
                since_last = time.perf_counter() - last_request_finished
                if last_request_finished and since_last < self.minimum_request_interval_seconds:
                    time.sleep(self.minimum_request_interval_seconds - since_last)
                request_started = time.perf_counter()
                api_calls += 1
                try:
                    fetched = self.provider.fetch_trade_date(day)
                except Exception as exc:
                    flush()
                    error_type, safe_message = classify_tushare_error(exc)
                    connection.execute(
                        """
                        UPDATE sentiment_v3_adj_factor_backfill_items
                        SET status = 'FAILED', error_type = ?,
                            error_message_sanitized = ?, updated_at = ?
                        WHERE run_id = ? AND trade_date = ?
                        """,
                        [error_type, safe_message, datetime.now(tz=SHANGHAI), run_id, day],
                    )
                    break
                elapsed_request = (time.perf_counter() - request_started) * 1000
                request_ms += elapsed_request
                last_request_finished = time.perf_counter()
                missing = self._missing_symbols(connection, day)
                normalize_started = time.perf_counter()
                normalized = self._normalize_rows(
                    fetched,
                    trade_date=day,
                    missing_symbols=missing,
                    fetched_at=datetime.now(tz=SHANGHAI),
                )
                normalization_ms += (time.perf_counter() - normalize_started) * 1000
                digest = stable_hash([
                    [item[1], str(item[2]), item[3]] for item in normalized
                ])
                buffer.append((day, len(fetched), normalized, elapsed_request, digest))
                fetched_total += len(fetched)
                normalized_total += len(normalized)
                if len(buffer) >= self.database_batch_dates:
                    flush()
            flush()

            summary = connection.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'SUCCESS'),
                       COUNT(*) FILTER (WHERE status = 'FAILED'),
                       COALESCE(SUM(fetched_rows), 0),
                       COALESCE(SUM(normalized_rows), 0),
                       COALESCE(SUM(inserted_rows), 0)
                FROM sentiment_v3_adj_factor_backfill_items WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            requested, completed, failed, durable_fetched, durable_normalized, durable_inserted = (
                int(value) for value in summary
            )
            status = "COMPLETED" if requested == completed else (
                "FAILED" if failed else "PARTIAL"
            )
            completed_at = datetime.now(tz=SHANGHAI) if status == "COMPLETED" else None
            connection.execute(
                """
                UPDATE sentiment_v3_adj_factor_backfill_runs
                SET status = ?, requested_dates = ?, completed_dates = ?,
                    failed_dates = ?, api_calls = ?, fetched_rows = ?,
                    normalized_rows = ?, inserted_rows = ?,
                    completed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                [
                    status, requested, completed, failed, completed + failed,
                    durable_fetched, durable_normalized, durable_inserted, completed_at,
                    datetime.now(tz=SHANGHAI), run_id,
                ],
            )
        return BackfillMetrics(
            run_id=run_id,
            status=status,
            requested_dates=requested,
            completed_dates=completed,
            failed_dates=failed,
            api_calls=api_calls,
            fetched_rows=fetched_total,
            normalized_rows=normalized_total,
            inserted_rows=inserted_total,
            request_ms=request_ms,
            normalization_ms=normalization_ms,
            database_write_ms=write_ms,
            total_ms=(time.perf_counter() - started) * 1000,
        )


__all__ = ["BatchedAdjustmentFactorBackfill"]
