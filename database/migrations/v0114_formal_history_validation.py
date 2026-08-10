from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0114_formal_history_validation"
SCHEMA_VERSION = "formal-history-validation-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS historical_adjustment_factors (
        factor_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        trade_date DATE NOT NULL,
        adj_factor DOUBLE NOT NULL CHECK (adj_factor > 0),
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (symbol, trade_date, source, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_security_statuses (
        status_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        status_type VARCHAR NOT NULL CHECK (
            status_type IN ('ST', 'SUSPENDED', 'DELISTED')
        ),
        status_value BOOLEAN NOT NULL,
        effective_start DATE NOT NULL,
        effective_end DATE,
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (symbol, status_type, effective_start, source, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_benchmark_bars (
        bar_id VARCHAR PRIMARY KEY,
        benchmark_code VARCHAR NOT NULL,
        benchmark_name VARCHAR,
        benchmark_type VARCHAR NOT NULL CHECK (
            benchmark_type IN ('CSI300', 'INDUSTRY')
        ),
        trade_date DATE NOT NULL,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE NOT NULL CHECK (close > 0),
        volume DOUBLE,
        amount DOUBLE,
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (benchmark_code, trade_date, source, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_industry_memberships (
        membership_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        industry_code VARCHAR NOT NULL,
        industry_name VARCHAR,
        valid_from DATE NOT NULL,
        valid_to DATE,
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (symbol, industry_code, valid_from, source, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_risk_events (
        event_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        title VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        source_url VARCHAR,
        content_hash VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (symbol, event_time, source, content_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_adjustment_factor_symbol_date
    ON historical_adjustment_factors (symbol, trade_date, data_available_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_security_status_symbol_date
    ON historical_security_statuses (
        symbol, effective_start, effective_end, data_available_time
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_benchmark_code_date
    ON historical_benchmark_bars (
        benchmark_code, trade_date, data_available_time
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_industry_membership_symbol_date
    ON historical_industry_memberships (
        symbol, valid_from, valid_to, data_available_time
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risk_event_symbol_time
    ON historical_risk_events (symbol, data_available_time, event_type)
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