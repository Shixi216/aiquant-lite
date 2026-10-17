from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as wall_time
from typing import Any, Callable, Protocol, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from data_hub.providers.tushare_provider import (
    TushareProvider,
    classify_tushare_error,
)
from data_hub.services.full_market_common import (
    normalize_symbol,
    parse_date,
    stable_hash,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
AUDIT_VERSION = "ADJ_FACTOR_COVERAGE_AUDIT_V1"
BACKFILL_VERSION = "ADJ_FACTOR_BACKFILL_V1"
FACTOR_SOURCE = "Tushare Pro"
_A_SHARE_PATTERN = r"^[0-9]{6}\.(SH|SZ|BJ)$"


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _connection(factory: Callable[[], Any]) -> Any:
    value = factory()
    return value if hasattr(value, "__enter__") else nullcontext(value)


@dataclass(frozen=True)
class CoverageSlice:
    group: str
    raw_bar_symbols: int
    adj_factor_symbols: int
    matched_symbols: int
    fully_covered_symbols: int
    partial_symbols: int
    missing_symbols: int
    raw_bar_rows: int
    matched_rows: int
    missing_rows: int
    symbol_coverage_ratio: float
    row_coverage_ratio: float


@dataclass(frozen=True)
class CoverageAudit:
    audit_version: str
    audited_at: datetime
    date_from: date | None
    date_to: date | None
    overall: CoverageSlice
    by_year: tuple[CoverageSlice, ...]
    by_exchange: tuple[CoverageSlice, ...]
    by_board: tuple[CoverageSlice, ...]
    by_listing_status: tuple[CoverageSlice, ...]
    by_current_st: tuple[CoverageSlice, ...]
    by_listing_recency: tuple[CoverageSlice, ...]
    historical_st: CoverageSlice
    invalid_factor_rows: int
    duplicate_factor_pairs: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdjustmentFactorCoverageAuditor:
    """Read-only symbol and symbol-date coverage audit over canonical RAW bars."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self.connection_factory = connection_factory

    @staticmethod
    def _prepare(connection: Any) -> None:
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW _s3a1_raw AS
            SELECT symbol, trade_date
            FROM canonical_historical_bars
            WHERE adjustment_type = 'RAW'
              AND verification_status <> 'CONFLICT'
              AND regexp_matches(symbol, '{_A_SHARE_PATTERN}')
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, trade_date
                ORDER BY generated_at DESC, bar_id
            ) = 1
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW _s3a1_factor AS
            SELECT symbol, trade_date, adj_factor
            FROM historical_adjustment_factors
            WHERE adj_factor > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, trade_date
                ORDER BY fetched_at DESC, factor_id
            ) = 1
            """
        )

    @staticmethod
    def _slice(
        connection: Any,
        group_sql: str,
        group_value: Any,
        *,
        raw_view: str = "_s3a1_raw",
    ) -> CoverageSlice:
        row = connection.execute(
            f"""
            WITH scoped AS (
                SELECT r.symbol, r.trade_date, {group_sql} AS group_name,
                       f.adj_factor IS NOT NULL AS matched,
                       fs.symbol IS NOT NULL AS has_any_factor
                FROM {raw_view} r
                LEFT JOIN _s3a1_factor f USING (symbol, trade_date)
                LEFT JOIN (
                    SELECT DISTINCT symbol FROM _s3a1_factor
                ) fs USING (symbol)
            ), per_symbol AS (
                SELECT group_name, symbol, COUNT(*) AS raw_rows,
                       COUNT(*) FILTER (WHERE matched) AS matched_rows,
                       BOOL_OR(has_any_factor) AS has_any_factor
                FROM scoped
                WHERE group_name = ?
                GROUP BY group_name, symbol
            )
            SELECT group_name,
                   COUNT(*) AS raw_symbols,
                   COUNT(*) FILTER (WHERE has_any_factor) AS factor_symbols,
                   COUNT(*) FILTER (WHERE matched_rows > 0) AS matched_symbols,
                   COUNT(*) FILTER (WHERE matched_rows = raw_rows) AS full_symbols,
                   COUNT(*) FILTER (
                       WHERE matched_rows > 0 AND matched_rows < raw_rows
                   ) AS partial_symbols,
                   COUNT(*) FILTER (WHERE matched_rows = 0) AS missing_symbols,
                   SUM(raw_rows) AS raw_rows,
                   SUM(matched_rows) AS matched_rows
            FROM per_symbol
            GROUP BY group_name
            """,
            [group_value],
        ).fetchone()
        if row is None:
            return CoverageSlice(str(group_value), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0.0)
        (
            group, raw_symbols, factor_symbols, matched_symbols, full_symbols,
            partial_symbols, missing_symbols, raw_rows, matched_rows,
        ) = row
        raw_symbols = int(raw_symbols)
        raw_rows = int(raw_rows)
        matched_rows = int(matched_rows)
        return CoverageSlice(
            group=str(group),
            raw_bar_symbols=raw_symbols,
            adj_factor_symbols=int(factor_symbols),
            matched_symbols=int(matched_symbols),
            fully_covered_symbols=int(full_symbols),
            partial_symbols=int(partial_symbols),
            missing_symbols=int(missing_symbols),
            raw_bar_rows=raw_rows,
            matched_rows=matched_rows,
            missing_rows=raw_rows - matched_rows,
            symbol_coverage_ratio=_ratio(int(matched_symbols), raw_symbols),
            row_coverage_ratio=_ratio(matched_rows, raw_rows),
        )

    @classmethod
    def _groups(
        cls,
        connection: Any,
        group_sql: str,
        *,
        raw_view: str = "_s3a1_raw",
    ) -> tuple[CoverageSlice, ...]:
        values = [
            row[0]
            for row in connection.execute(
                f"SELECT DISTINCT {group_sql} AS group_name FROM {raw_view} r ORDER BY 1"
            ).fetchall()
        ]
        return tuple(
            cls._slice(connection, group_sql, value, raw_view=raw_view)
            for value in values
        )

    def run(self) -> CoverageAudit:
        with _connection(self.connection_factory) as connection:
            self._prepare(connection)
            date_from, date_to = connection.execute(
                "SELECT MIN(trade_date), MAX(trade_date) FROM _s3a1_raw"
            ).fetchone()
            universe_version = connection.execute(
                "SELECT MAX(universe_version) FROM stock_universe"
            ).fetchone()[0]
            master_join = (
                "LEFT JOIN stock_universe u ON u.symbol = r.symbol "
                f"AND u.universe_version = '{universe_version}'"
            )
            connection.execute(
                f"""
                CREATE OR REPLACE TEMP VIEW _s3a1_raw_master AS
                SELECT r.*, u.exchange, u.board, u.listing_status, u.is_st,
                       u.list_date, u.delist_date
                FROM _s3a1_raw r {master_join}
                """
            )
            master_view = "_s3a1_raw_master"
            overall = self._slice(connection, "'ALL'", "ALL", raw_view=master_view)
            by_year = self._groups(
                connection, "CAST(year(r.trade_date) AS VARCHAR)", raw_view=master_view
            )
            by_exchange = self._groups(
                connection,
                "COALESCE(r.exchange, split_part(r.symbol, '.', 2))",
                raw_view=master_view,
            )
            by_board = self._groups(
                connection, "COALESCE(r.board, 'UNKNOWN')", raw_view=master_view
            )
            by_status = self._groups(
                connection,
                "COALESCE(r.listing_status, 'UNKNOWN')",
                raw_view=master_view,
            )
            by_st = self._groups(
                connection,
                "CASE WHEN r.is_st THEN 'ST' ELSE 'NON_ST' END",
                raw_view=master_view,
            )
            recency = self._groups(
                connection,
                "CASE WHEN r.list_date IS NULL THEN 'UNKNOWN' "
                "WHEN r.list_date >= current_date - INTERVAL 1 YEAR THEN 'LAST_1Y' "
                "WHEN r.list_date >= current_date - INTERVAL 2 YEAR THEN 'LAST_2Y' "
                "ELSE 'OLDER' END",
                raw_view=master_view,
            )
            connection.execute(
                """
                CREATE OR REPLACE TEMP VIEW _s3a1_raw_st AS
                SELECT DISTINCT r.symbol, r.trade_date
                FROM _s3a1_raw_master r
                JOIN historical_security_statuses s ON s.symbol = r.symbol
                 AND s.status_type = 'ST' AND s.status_value
                 AND r.trade_date >= s.effective_start
                 AND (s.effective_end IS NULL OR r.trade_date <= s.effective_end)
                """
            )
            historical_st = self._slice(
                connection,
                "'HISTORICAL_ST'",
                "HISTORICAL_ST",
                raw_view="_s3a1_raw_st",
            )
            invalid = int(connection.execute(
                "SELECT COUNT(*) FROM historical_adjustment_factors WHERE adj_factor IS NULL OR adj_factor <= 0"
            ).fetchone()[0])
            duplicates = int(connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT symbol, trade_date, COUNT(*) AS count_rows
                    FROM historical_adjustment_factors
                    GROUP BY symbol, trade_date HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0])
        return CoverageAudit(
            audit_version=AUDIT_VERSION,
            audited_at=datetime.now(tz=SHANGHAI),
            date_from=date_from,
            date_to=date_to,
            overall=overall,
            by_year=by_year,
            by_exchange=by_exchange,
            by_board=by_board,
            by_listing_status=by_status,
            by_current_st=by_st,
            by_listing_recency=recency,
            historical_st=historical_st,
            invalid_factor_rows=invalid,
            duplicate_factor_pairs=duplicates,
        )


class AdjustmentFactorDateProvider(Protocol):
    source: str

    def fetch_trade_date(self, trade_date: date) -> list[dict[str, Any]]: ...


class TushareAdjustmentFactorDateProvider:
    """Narrow adapter over the existing Tushare client for market-wide batches."""

    source = FACTOR_SOURCE

    def __init__(self, provider: TushareProvider | None = None) -> None:
        self._provider = provider or TushareProvider()

    def fetch_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        frame = self._provider._client.adj_factor(  # noqa: SLF001
            trade_date=trade_date.strftime("%Y%m%d")
        )
        return self._provider._frame_rows(  # noqa: SLF001
            frame,
            required={"ts_code", "trade_date", "adj_factor"},
            capability="TUSHARE_ADJ_FACTOR",
        )


@dataclass(frozen=True)
class BackfillMetrics:
    run_id: str
    status: str
    requested_dates: int
    completed_dates: int
    failed_dates: int
    api_calls: int
    fetched_rows: int
    normalized_rows: int
    inserted_rows: int
    request_ms: float
    normalization_ms: float
    database_write_ms: float
    total_ms: float


class AdjustmentFactorBackfill:
    """Single-writer, date-batched, checkpointed and idempotent repair."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        provider: AdjustmentFactorDateProvider,
        minimum_request_interval_seconds: float = 0.20,
    ) -> None:
        self.connection_factory = connection_factory
        self.provider = provider
        self.minimum_request_interval_seconds = max(
            0.0, minimum_request_interval_seconds
        )

    @staticmethod
    def ensure_metadata_tables(connection: Any) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sentiment_v3_adj_factor_backfill_runs (
                run_id VARCHAR PRIMARY KEY,
                data_version VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                date_from DATE NOT NULL,
                date_to DATE NOT NULL,
                status VARCHAR NOT NULL,
                requested_dates BIGINT NOT NULL,
                completed_dates BIGINT NOT NULL,
                failed_dates BIGINT NOT NULL,
                api_calls BIGINT NOT NULL,
                fetched_rows BIGINT NOT NULL,
                normalized_rows BIGINT NOT NULL,
                inserted_rows BIGINT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sentiment_v3_adj_factor_backfill_items (
                run_id VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                status VARCHAR NOT NULL,
                eligible_rows BIGINT NOT NULL,
                existing_rows BIGINT NOT NULL,
                fetched_rows BIGINT NOT NULL DEFAULT 0,
                normalized_rows BIGINT NOT NULL DEFAULT 0,
                inserted_rows BIGINT NOT NULL DEFAULT 0,
                request_latency_ms DOUBLE NOT NULL DEFAULT 0,
                error_type VARCHAR,
                error_message_sanitized VARCHAR,
                content_hash VARCHAR,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (run_id, trade_date)
            )
            """
        )

    @staticmethod
    def _eligible_dates(connection: Any, start: date, end: date) -> list[tuple[date, int, int]]:
        return [
            (row[0], int(row[1]), int(row[2]))
            for row in connection.execute(
                f"""
                WITH raw AS (
                    SELECT symbol, trade_date
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND regexp_matches(symbol, '{_A_SHARE_PATTERN}')
                      AND trade_date BETWEEN ? AND ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY generated_at DESC, bar_id
                    ) = 1
                ), factors AS (
                    SELECT symbol, trade_date
                    FROM historical_adjustment_factors
                    WHERE adj_factor > 0 AND trade_date BETWEEN ? AND ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY fetched_at DESC, factor_id
                    ) = 1
                )
                SELECT r.trade_date, COUNT(*) AS eligible_rows,
                       COUNT(f.symbol) AS existing_rows
                FROM raw r LEFT JOIN factors f USING (symbol, trade_date)
                GROUP BY r.trade_date
                HAVING COUNT(f.symbol) < COUNT(*)
                ORDER BY r.trade_date
                """,
                [start, end, start, end],
            ).fetchall()
        ]

    @staticmethod
    def _missing_symbols(connection: Any, trade_date: date) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                f"""
                WITH raw AS (
                    SELECT symbol
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND regexp_matches(symbol, '{_A_SHARE_PATTERN}')
                      AND trade_date = ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY generated_at DESC, bar_id
                    ) = 1
                ), factors AS (
                    SELECT symbol
                    FROM historical_adjustment_factors
                    WHERE adj_factor > 0 AND trade_date = ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY fetched_at DESC, factor_id
                    ) = 1
                )
                SELECT r.symbol FROM raw r LEFT JOIN factors f USING (symbol)
                WHERE f.symbol IS NULL ORDER BY r.symbol
                """,
                [trade_date, trade_date],
            ).fetchall()
        }

    @staticmethod
    def _normalize_rows(
        rows: Sequence[dict[str, Any]],
        *,
        trade_date: date,
        missing_symbols: set[str],
        fetched_at: datetime,
    ) -> list[list[Any]]:
        values: list[list[Any]] = []
        seen: set[tuple[str, date]] = set()
        for raw in rows:
            symbol = normalize_symbol(raw.get("ts_code"))
            day = parse_date(raw.get("trade_date"))
            try:
                factor = float(raw.get("adj_factor"))
            except (TypeError, ValueError):
                continue
            key = (symbol or "", day or trade_date)
            if (
                symbol not in missing_symbols
                or day != trade_date
                or factor <= 0
                or key in seen
            ):
                continue
            seen.add(key)
            available = datetime.combine(day, wall_time(hour=16), tzinfo=SHANGHAI)
            digest = stable_hash({"kind": "ADJ_FACTOR", "raw": raw})
            values.append([
                f"adj_{digest[:32]}", symbol, day, factor,
                available, available, available, fetched_at,
                FACTOR_SOURCE, digest,
                json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str),
            ])
        return values

    @staticmethod
    def _insert_rows(connection: Any, rows: list[list[Any]]) -> int:
        if not rows:
            return 0
        columns = [
            "factor_id", "symbol", "trade_date", "adj_factor", "event_time",
            "data_available_time", "data_cutoff", "fetched_at", "source",
            "content_hash", "raw_payload_json",
        ]
        frame = pd.DataFrame.from_records(rows, columns=columns)
        connection.register("_s3a1_factor_rows", frame)
        try:
            quoted = ", ".join(f'"{column}"' for column in columns)
            connection.execute(
                f"""
                INSERT INTO historical_adjustment_factors ({quoted})
                SELECT {quoted} FROM _s3a1_factor_rows
                ON CONFLICT DO NOTHING
                """
            )
        finally:
            connection.unregister("_s3a1_factor_rows")
        # Rows are pre-filtered against the live table while this single writer
        # owns the connection; duplicates inside a provider batch are removed by
        # the stable key and are not expected from the market-wide endpoint.
        return len(rows)

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
            for day in pending:
                since_last = time.perf_counter() - last_request_finished
                if last_request_finished and since_last < self.minimum_request_interval_seconds:
                    time.sleep(self.minimum_request_interval_seconds - since_last)
                request_started = time.perf_counter()
                api_calls += 1
                try:
                    fetched = self.provider.fetch_trade_date(day)
                    elapsed_request = (time.perf_counter() - request_started) * 1000
                    request_ms += elapsed_request
                    last_request_finished = time.perf_counter()
                    missing = self._missing_symbols(connection, day)
                    normalize_started = time.perf_counter()
                    fetched_at = datetime.now(tz=SHANGHAI)
                    normalized = self._normalize_rows(
                        fetched,
                        trade_date=day,
                        missing_symbols=missing,
                        fetched_at=fetched_at,
                    )
                    normalization_ms += (time.perf_counter() - normalize_started) * 1000
                    write_started = time.perf_counter()
                    inserted = self._insert_rows(connection, normalized)
                    digest = stable_hash([
                        [item[1], str(item[2]), item[3]] for item in normalized
                    ])
                    connection.execute(
                        """
                        UPDATE sentiment_v3_adj_factor_backfill_items
                        SET status = 'SUCCESS', fetched_rows = ?, normalized_rows = ?,
                            inserted_rows = ?, request_latency_ms = ?,
                            error_type = NULL, error_message_sanitized = NULL,
                            content_hash = ?, updated_at = ?
                        WHERE run_id = ? AND trade_date = ?
                        """,
                        [
                            len(fetched), len(normalized), inserted, elapsed_request,
                            digest, datetime.now(tz=SHANGHAI), run_id, day,
                        ],
                    )
                    write_ms += (time.perf_counter() - write_started) * 1000
                    fetched_total += len(fetched)
                    normalized_total += len(normalized)
                    inserted_total += inserted
                except Exception as exc:
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
            summary = connection.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'SUCCESS'),
                       COUNT(*) FILTER (WHERE status = 'FAILED')
                FROM sentiment_v3_adj_factor_backfill_items WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            requested, completed, failed = (int(value) for value in summary)
            status = "COMPLETED" if requested == completed else (
                "FAILED" if failed else "PARTIAL"
            )
            completed_at = datetime.now(tz=SHANGHAI) if status == "COMPLETED" else None
            connection.execute(
                """
                UPDATE sentiment_v3_adj_factor_backfill_runs
                SET status = ?, requested_dates = ?, completed_dates = ?,
                    failed_dates = ?, api_calls = api_calls + ?,
                    fetched_rows = fetched_rows + ?,
                    normalized_rows = normalized_rows + ?,
                    inserted_rows = inserted_rows + ?, completed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                [
                    status, requested, completed, failed, api_calls, fetched_total,
                    normalized_total, inserted_total, completed_at,
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


__all__ = [
    "AUDIT_VERSION",
    "BACKFILL_VERSION",
    "AdjustmentFactorBackfill",
    "AdjustmentFactorCoverageAuditor",
    "BackfillMetrics",
    "CoverageAudit",
    "CoverageSlice",
    "TushareAdjustmentFactorDateProvider",
]
