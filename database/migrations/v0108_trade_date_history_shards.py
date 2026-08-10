from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0108_trade_date_history_shards"
SCHEMA_VERSION = "trade-date-history-shards-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS historical_trade_date_shards (
        shard_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        shard_index BIGINT NOT NULL,
        shard_type VARCHAR NOT NULL
            CHECK (shard_type = 'TRADE_DATE_SHARD'),
        provider VARCHAR NOT NULL,
        trade_date DATE NOT NULL,
        adjustment_type VARCHAR NOT NULL
            CHECK (adjustment_type = 'RAW'),
        expected_universe_count BIGINT NOT NULL,
        returned_record_count BIGINT NOT NULL,
        valid_record_count BIGINT NOT NULL,
        invalid_record_count BIGINT NOT NULL,
        missing_symbol_count BIGINT NOT NULL,
        extra_symbol_count BIGINT NOT NULL,
        beijing_record_count BIGINT NOT NULL,
        shanghai_record_count BIGINT NOT NULL,
        shenzhen_record_count BIGINT NOT NULL,
        raw_inserted_count BIGINT NOT NULL,
        canonical_inserted_count BIGINT NOT NULL,
        duplicate_count BIGINT NOT NULL,
        conflict_count BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        retry_count BIGINT NOT NULL,
        latency_ms BIGINT NOT NULL,
        write_elapsed_seconds DOUBLE,
        database_growth_bytes BIGINT,
        coverage_ratio DOUBLE NOT NULL
            CHECK (coverage_ratio >= 0 AND coverage_ratio <= 1),
        status VARCHAR NOT NULL
            CHECK (
                status IN (
                    'PENDING', 'RUNNING', 'SUCCESS', 'SUCCESS_PARTIAL',
                    'SUCCESS_EMPTY', 'FAILED', 'PAUSED_BUDGET',
                    'PAUSED_RATE_LIMIT', 'CANCELLED'
                )
            ),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        error_code VARCHAR,
        sanitized_error VARCHAR,
        payload_json JSON NOT NULL,
        UNIQUE (run_id, trade_date)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trade_date_shard_run
    ON historical_trade_date_shards (run_id, shard_index, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trade_date_shard_date
    ON historical_trade_date_shards (
        provider, trade_date, adjustment_type, status
    )
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    payload += f"\n{SCHEMA_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_migration(connection: duckdb.DuckDBPyConnection) -> bool:
    checksum = _checksum()
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id VARCHAR PRIMARY KEY,
                checksum VARCHAR NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = ?",
            [MIGRATION_ID],
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RuntimeError(
                    f"Migration checksum mismatch for {MIGRATION_ID}"
                )
            connection.execute("COMMIT")
            return False
        for statement in MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_id, checksum, applied_at)
            VALUES (?, ?, ?)
            """,
            [MIGRATION_ID, checksum, datetime.now().astimezone()],
        )
        connection.execute("COMMIT")
        return True
    except Exception:
        connection.execute("ROLLBACK")
        raise


__all__ = [
    "MIGRATION_ID",
    "MIGRATION_STATEMENTS",
    "SCHEMA_VERSION",
    "apply_migration",
]
