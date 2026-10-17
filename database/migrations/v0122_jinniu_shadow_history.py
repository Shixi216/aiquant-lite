from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0122_jinniu_shadow_history"
SCHEMA_VERSION = "jinniu-shadow-history-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS jinniu_shadow_records (
        record_id VARCHAR PRIMARY KEY,
        endpoint VARCHAR NOT NULL,
        natural_key VARCHAR NOT NULL,
        symbol VARCHAR,
        trade_date DATE,
        bar_time TIMESTAMPTZ,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume_shares DOUBLE,
        amount_cny DOUBLE,
        adj_factor DOUBLE,
        is_open BOOLEAN,
        provider VARCHAR NOT NULL CHECK (provider = 'Jinniu'),
        source_timestamp TIMESTAMPTZ NOT NULL,
        available_time TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        content_hash VARCHAR NOT NULL,
        quality VARCHAR NOT NULL,
        schema_version VARCHAR NOT NULL,
        payload_json JSON NOT NULL,
        UNIQUE (endpoint, natural_key, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jinniu_import_checkpoints (
        checkpoint_id VARCHAR PRIMARY KEY,
        endpoint VARCHAR NOT NULL,
        scope_hash VARCHAR NOT NULL,
        status VARCHAR NOT NULL CHECK (
            status IN ('PENDING', 'RUNNING', 'PAUSED', 'SUCCESS', 'FAILED')
        ),
        cursor_json JSON NOT NULL,
        persisted_count BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        error_type VARCHAR,
        UNIQUE (endpoint, scope_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jinniu_request_audits (
        audit_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        endpoint VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        http_status BIGINT,
        record_count BIGINT NOT NULL,
        latency_ms BIGINT NOT NULL,
        truncated BOOLEAN NOT NULL,
        retry_count BIGINT NOT NULL,
        error_type VARCHAR,
        requested_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL,
        request_hash VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jinniu_shadow_comparisons (
        comparison_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        endpoint VARCHAR NOT NULL,
        symbol VARCHAR,
        observation_time TIMESTAMPTZ NOT NULL,
        comparison_source VARCHAR NOT NULL,
        field_name VARCHAR NOT NULL,
        jinniu_value DOUBLE,
        comparison_value DOUBLE,
        absolute_difference DOUBLE,
        relative_difference DOUBLE,
        matched BOOLEAN,
        quality VARCHAR NOT NULL,
        payload_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jinniu_route_gate (
        gate_name VARCHAR PRIMARY KEY,
        validated BOOLEAN NOT NULL,
        audit_run_id VARCHAR,
        validated_at TIMESTAMPTZ,
        primary_provider VARCHAR NOT NULL,
        fallback_provider VARCHAR NOT NULL,
        validation_provider VARCHAR NOT NULL,
        reason VARCHAR NOT NULL
    )
    """,
    """
    INSERT INTO jinniu_route_gate
    SELECT
        'HISTORICAL_MARKET', FALSE, NULL, NULL,
        'Jinniu', 'Tushare', 'BaoStock',
        'shadow validation has not passed'
    WHERE NOT EXISTS (
        SELECT 1 FROM jinniu_route_gate
        WHERE gate_name = 'HISTORICAL_MARKET'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jinniu_shadow_endpoint_symbol_time
    ON jinniu_shadow_records (endpoint, symbol, source_timestamp)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jinniu_shadow_trade_date
    ON jinniu_shadow_records (trade_date, endpoint, symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jinniu_comparison_run_field
    ON jinniu_shadow_comparisons (run_id, field_name, matched)
    """,
)


def _checksum() -> str:
    payload = "\n".join(item.strip() for item in MIGRATION_STATEMENTS)
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
