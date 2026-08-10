from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0110_market_scanner_v1"
SCHEMA_VERSION = "market-scanner-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS scanner_query_plans (
        query_id VARCHAR PRIMARY KEY,
        original_query VARCHAR NOT NULL,
        normalized_query VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        parser_type VARCHAR NOT NULL,
        parser_version VARCHAR NOT NULL,
        plan_hash VARCHAR NOT NULL UNIQUE,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_runs (
        run_id VARCHAR PRIMARY KEY,
        query_id VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        snapshot_id VARCHAR,
        universe_count BIGINT NOT NULL,
        scanned_count BIGINT NOT NULL,
        matched_count BIGINT NOT NULL,
        returned_count BIGINT NOT NULL,
        parser_type VARCHAR NOT NULL,
        parser_version VARCHAR NOT NULL,
        scanner_version VARCHAR NOT NULL,
        plan_hash VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        database_query_count BIGINT NOT NULL,
        network_request_count BIGINT NOT NULL CHECK (network_request_count = 0),
        model_call_count BIGINT NOT NULL CHECK (model_call_count = 0),
        elapsed_ms DOUBLE NOT NULL,
        peak_memory_bytes BIGINT NOT NULL,
        status VARCHAR NOT NULL,
        risk_flags_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (plan_hash, input_snapshot_hash, analysis_mode)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_run_filters (
        run_id VARCHAR NOT NULL,
        filter_index BIGINT NOT NULL,
        filter_type VARCHAR NOT NULL,
        filter_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (run_id, filter_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_candidates (
        run_id VARCHAR NOT NULL,
        rank BIGINT NOT NULL,
        symbol VARCHAR NOT NULL,
        scanner_score DOUBLE NOT NULL CHECK (
            scanner_score >= 0 AND scanner_score <= 1
        ),
        technical_score DOUBLE,
        capital_flow_score DOUBLE,
        shadow_composite_score DOUBLE,
        composite_confidence DOUBLE,
        factor_coverage VARCHAR NOT NULL,
        anomaly_types_json JSON NOT NULL,
        reason_codes_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (run_id, rank),
        UNIQUE (run_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_candidate_reasons (
        run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        reason_index BIGINT NOT NULL,
        reason_code VARCHAR NOT NULL,
        evidence_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (run_id, symbol, reason_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_evaluations (
        evaluation_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        candidate_rank BIGINT NOT NULL,
        scanner_score DOUBLE NOT NULL,
        anomaly_types_json JSON NOT NULL,
        factor_coverage VARCHAR NOT NULL,
        composite_confidence DOUBLE,
        return_1d DOUBLE,
        return_3d DOUBLE,
        return_5d DOUBLE,
        return_20d DOUBLE,
        maximum_upside DOUBLE,
        maximum_drawdown DOUBLE,
        is_suspended BOOLEAN,
        price_limit_up BOOLEAN,
        price_limit_down BOOLEAN,
        data_complete BOOLEAN NOT NULL,
        status VARCHAR NOT NULL,
        analysis_time TIMESTAMPTZ NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, symbol, evaluated_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_plan_hash
    ON scanner_query_plans (plan_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_plan_generated
    ON scanner_query_plans (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_run_query
    ON scanner_runs (query_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_run_plan
    ON scanner_runs (plan_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_run_snapshot
    ON scanner_runs (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_run_generated
    ON scanner_runs (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_candidate_symbol
    ON scanner_candidates (symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_candidate_run
    ON scanner_candidates (run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_candidate_generated
    ON scanner_candidates (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_reason_symbol
    ON scanner_candidate_reasons (symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_evaluation_run
    ON scanner_evaluations (run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_evaluation_symbol
    ON scanner_evaluations (symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scanner_evaluation_generated
    ON scanner_evaluations (generated_at)
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    payload += f"\n{SCHEMA_VERSION}"
    return hashlib.sha256(payload.encode()).hexdigest()


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
