from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0121_sentiment_v3_live_shadow"
SCHEMA_VERSION = "sentiment-v3-live-shadow-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sentiment_v3_live_shadow_observations (
        observation_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        trade_date DATE NOT NULL,
        cutoff TIMESTAMPTZ NOT NULL,
        "window" VARCHAR NOT NULL,
        method VARCHAR NOT NULL DEFAULT 'EQUAL_WEIGHT',
        research_score DOUBLE,
        confidence DOUBLE NOT NULL,
        risk_reminder_triggered BOOLEAN NOT NULL DEFAULT FALSE,
        risk_reminder_details VARCHAR DEFAULT '',
        feature_values VARCHAR NOT NULL,
        feature_quality VARCHAR NOT NULL,
        provider_latency_ms DOUBLE DEFAULT 0,
        data_coverage_ratio DOUBLE DEFAULT 0,
        content_hash VARCHAR NOT NULL,
        formal_weight INTEGER NOT NULL DEFAULT 0,
        research_only BOOLEAN NOT NULL DEFAULT TRUE,
        creates_trade BOOLEAN NOT NULL DEFAULT FALSE,
        observed_at TIMESTAMPTZ NOT NULL,
        algorithm_version VARCHAR NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sv3_live_shadow_symbol_date
    ON sentiment_v3_live_shadow_observations (symbol, trade_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sv3_live_shadow_run_id
    ON sentiment_v3_live_shadow_observations (run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_v3_drift_reports (
        report_id VARCHAR PRIMARY KEY,
        trade_date DATE NOT NULL,
        "window" VARCHAR NOT NULL,
        feature_name VARCHAR NOT NULL,
        live_mean DOUBLE,
        live_std DOUBLE,
        replay_mean DOUBLE,
        replay_std DOUBLE,
        drift_zscore DOUBLE,
        drift_status VARCHAR NOT NULL,
        provider_used VARCHAR DEFAULT '',
        reported_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sv3_drift_date
    ON sentiment_v3_drift_reports (trade_date, "window")
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MIGRATION_CHECKSUM = _checksum()


def apply_migration(connection: duckdb.DuckDBPyConnection) -> bool:
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
        if existing[0] != MIGRATION_CHECKSUM:
            raise RuntimeError(
                f"migration checksum mismatch for {MIGRATION_ID}"
            )
        return False
    for statement in MIGRATION_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, ?, ?)",
        [MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now().astimezone()],
    )
    return True


__all__ = [
    "MIGRATION_CHECKSUM",
    "MIGRATION_ID",
    "SCHEMA_VERSION",
    "apply_migration",
]
