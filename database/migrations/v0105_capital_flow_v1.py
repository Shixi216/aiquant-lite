from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0105_capital_flow_v1"
SCHEMA_VERSION = "capital-flow-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS capital_flow_symbol_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        score DOUBLE NOT NULL CHECK (score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        volume_score DOUBLE,
        amount_score DOUBLE,
        turnover_score DOUBLE,
        price_volume_score DOUBLE,
        financing_score DOUBLE,
        sector_flow_score DOUBLE,
        liquidity_score DOUBLE,
        volume_ratio_20d DOUBLE,
        amount_ratio_20d DOUBLE,
        turnover_rate DOUBLE,
        price_volume_state VARCHAR NOT NULL,
        financing_trend VARCHAR NOT NULL,
        amount_market_percentile DOUBLE,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        generated_at TIMESTAMPTZ NOT NULL,
        payload_json JSON NOT NULL,
        UNIQUE (
            symbol, analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capital_flow_sector_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        sector VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        score DOUBLE NOT NULL CHECK (score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        generated_at TIMESTAMPTZ NOT NULL,
        payload_json JSON NOT NULL,
        UNIQUE (
            sector, analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capital_flow_market_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        score DOUBLE NOT NULL CHECK (score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        generated_at TIMESTAMPTZ NOT NULL,
        payload_json JSON NOT NULL,
        UNIQUE (
            analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capital_flow_evaluations (
        evaluation_id VARCHAR PRIMARY KEY,
        snapshot_id VARCHAR NOT NULL,
        snapshot_time TIMESTAMPTZ NOT NULL,
        symbol VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        capital_score DOUBLE NOT NULL CHECK (capital_score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        price_volume_state VARCHAR NOT NULL,
        volume_ratio_20d DOUBLE,
        turnover_rate DOUBLE,
        return_1d DOUBLE,
        return_3d DOUBLE,
        return_5d DOUBLE,
        return_20d DOUBLE,
        max_rise DOUBLE,
        max_drawdown DOUBLE,
        was_limit_up BOOLEAN,
        was_limit_down BOOLEAN,
        was_suspended BOOLEAN,
        data_complete BOOLEAN NOT NULL,
        evidence_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        payload_json JSON NOT NULL,
        UNIQUE (snapshot_id, algorithm_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capital_flow_backfill_runs (
        backfill_run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        filters_json JSON NOT NULL,
        status VARCHAR NOT NULL,
        processed_count BIGINT NOT NULL,
        success_count BIGINT NOT NULL,
        skipped_count BIGINT NOT NULL,
        missing_count BIGINT NOT NULL,
        failed_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        report_path VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capital_flow_backfill_items (
        backfill_run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ,
        status VARCHAR NOT NULL,
        snapshot_id VARCHAR,
        error_type VARCHAR,
        error_message VARCHAR,
        processed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (backfill_run_id, symbol)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_symbol_cutoff
    ON capital_flow_symbol_snapshots (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_sector_cutoff
    ON capital_flow_sector_snapshots (sector, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_symbol_hash
    ON capital_flow_symbol_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_sector_hash
    ON capital_flow_sector_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_market_hash
    ON capital_flow_market_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capital_flow_symbol_generated
    ON capital_flow_symbol_snapshots (generated_at)
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
