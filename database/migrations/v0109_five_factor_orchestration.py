from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0109_five_factor_orchestration"
SCHEMA_VERSION = "five-factor-orchestration-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS factor_bundle_snapshots (
        bundle_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        available_factor_types_json JSON NOT NULL,
        missing_factor_types_json JSON NOT NULL,
        stale_factor_types_json JSON NOT NULL,
        conflicting_factor_types_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        shared_event_cluster_ids_json JSON NOT NULL,
        shared_evidence_groups_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            symbol, analysis_mode, data_cutoff, input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shadow_composite_snapshots (
        composite_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        factor_output_id VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        score DOUBLE NOT NULL CHECK (score >= -1 AND score <= 1),
        confidence DOUBLE NOT NULL
            CHECK (confidence >= 0 AND confidence <= 1),
        source_factor_output_ids_json JSON NOT NULL,
        shared_evidence_groups_json JSON NOT NULL,
        effective_weights_json JSON NOT NULL,
        correlation_discounts_json JSON NOT NULL,
        missing_factor_types_json JSON NOT NULL,
        stale_factor_types_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        formal_strategy_weight DOUBLE NOT NULL
            CHECK (formal_strategy_weight = 0),
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            symbol, analysis_mode, data_cutoff, input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS factor_correlation_audits (
        correlation_audit_id VARCHAR PRIMARY KEY,
        composite_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        factor_pair VARCHAR NOT NULL,
        source_factor_output_id VARCHAR NOT NULL,
        related_factor_output_id VARCHAR NOT NULL,
        shared_event_cluster_id VARCHAR,
        overlap_ratio DOUBLE NOT NULL
            CHECK (overlap_ratio >= 0 AND overlap_ratio <= 1),
        applied_discount DOUBLE NOT NULL
            CHECK (applied_discount >= 0 AND applied_discount <= 1),
        shared_evidence_ids_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (composite_id, factor_pair)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orchestration_evaluations (
        evaluation_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_time TIMESTAMPTZ NOT NULL,
        formal_score DOUBLE NOT NULL
            CHECK (formal_score >= -1 AND formal_score <= 1),
        formal_action VARCHAR NOT NULL,
        shadow_score DOUBLE NOT NULL
            CHECK (shadow_score >= -1 AND shadow_score <= 1),
        composite_confidence DOUBLE NOT NULL
            CHECK (
                composite_confidence >= 0
                AND composite_confidence <= 1
            ),
        factor_coverage_json JSON NOT NULL,
        effective_weights_json JSON NOT NULL,
        return_1d DOUBLE,
        return_3d DOUBLE,
        return_5d DOUBLE,
        return_20d DOUBLE,
        maximum_upside DOUBLE,
        maximum_drawdown DOUBLE,
        risk_vetoed BOOLEAN NOT NULL,
        data_complete BOOLEAN NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (symbol, analysis_time, input_snapshot_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_factor_bundle_symbol_cutoff
    ON factor_bundle_snapshots (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_factor_bundle_hash
    ON factor_bundle_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_factor_bundle_generated
    ON factor_bundle_snapshots (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_symbol_cutoff
    ON shadow_composite_snapshots (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_hash
    ON shadow_composite_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_source_factor
    ON shadow_composite_snapshots (factor_output_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_generated
    ON shadow_composite_snapshots (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_correlation_source_factor
    ON factor_correlation_audits (source_factor_output_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_correlation_shared_cluster
    ON factor_correlation_audits (shared_event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_correlation_generated
    ON factor_correlation_audits (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_symbol_time
    ON orchestration_evaluations (symbol, analysis_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_hash
    ON orchestration_evaluations (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_generated
    ON orchestration_evaluations (generated_at)
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
