from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0101_historical_backfill_audit"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS backfill_runs (
        backfill_run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('APPLY')),
        status VARCHAR NOT NULL
            CHECK (
                status IN (
                    'RUNNING', 'COMPLETED', 'PARTIAL',
                    'INTERRUPTED', 'FAILED'
                )
            ),
        filters_json JSON NOT NULL,
        original_record_count BIGINT NOT NULL,
        selected_record_count BIGINT NOT NULL,
        processed_count BIGINT NOT NULL,
        success_count BIGINT NOT NULL,
        skipped_count BIGINT NOT NULL,
        conflict_count BIGINT NOT NULL,
        failed_count BIGINT NOT NULL,
        report_json_path VARCHAR,
        report_markdown_path VARCHAR,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backfill_items (
        backfill_run_id VARCHAR NOT NULL,
        work_key VARCHAR NOT NULL,
        data_type VARCHAR NOT NULL,
        symbol VARCHAR,
        source_record_ids_json JSON NOT NULL,
        raw_record_count BIGINT NOT NULL,
        status VARCHAR NOT NULL
            CHECK (
                status IN (
                    'APPLIED', 'VERIFIED', 'SINGLE_SOURCE',
                    'CONFLICT', 'SKIPPED', 'FAILED'
                )
            ),
        result_type VARCHAR,
        result_id VARCHAR,
        reason VARCHAR,
        details_json JSON NOT NULL,
        processed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (backfill_run_id, work_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_backfill_runs_status
    ON backfill_runs (status, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_backfill_items_status
    ON backfill_items (backfill_run_id, status)
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
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
            """
            SELECT checksum
            FROM schema_migrations
            WHERE migration_id = ?
            """,
            [MIGRATION_ID],
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RuntimeError(
                    f"migration checksum mismatch for {MIGRATION_ID}"
                )
            connection.execute("COMMIT")
            return False

        for statement in MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (
                migration_id, checksum, applied_at
            )
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
    "apply_migration",
]
