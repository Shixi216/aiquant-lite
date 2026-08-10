from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0100_unified_data_layers"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS canonical_market_records (
        canonical_record_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        data_type VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        primary_source VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        verification_source_ids_json JSON NOT NULL,
        verification_status VARCHAR NOT NULL
            CHECK (
                verification_status IN (
                    'VERIFIED', 'CONFLICT', 'SINGLE_SOURCE'
                )
            ),
        field_differences_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        confidence DOUBLE NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        content_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        UNIQUE (symbol, data_type, event_time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS canonical_financial_records (
        canonical_record_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        data_type VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        primary_source VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        verification_source_ids_json JSON NOT NULL,
        verification_status VARCHAR NOT NULL
            CHECK (
                verification_status IN (
                    'VERIFIED', 'CONFLICT', 'SINGLE_SOURCE'
                )
            ),
        field_differences_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        confidence DOUBLE NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        content_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        UNIQUE (symbol, data_type, event_time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_clusters (
        event_cluster_id VARCHAR PRIMARY KEY,
        canonical_title VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        primary_source_id VARCHAR NOT NULL,
        source_count BIGINT NOT NULL CHECK (source_count >= 1),
        dedup_method VARCHAR NOT NULL,
        dedup_version VARCHAR NOT NULL,
        cluster_hash VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_source_links (
        event_cluster_id VARCHAR NOT NULL,
        source_record_id VARCHAR NOT NULL UNIQUE,
        source_level VARCHAR NOT NULL,
        is_primary BOOLEAN NOT NULL,
        linked_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (event_cluster_id, source_record_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_symbol_links (
        event_cluster_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        PRIMARY KEY (event_cluster_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_sector_links (
        event_cluster_id VARCHAR NOT NULL,
        sector VARCHAR NOT NULL,
        PRIMARY KEY (event_cluster_id, sector)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS factor_outputs (
        factor_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        factor_type VARCHAR NOT NULL
            CHECK (
                factor_type IN (
                    'TECHNICAL',
                    'FUNDAMENTAL',
                    'SENTIMENT',
                    'POLICY_NEWS',
                    'CAPITAL_FLOW'
                )
            ),
        score DOUBLE NOT NULL CHECK (score >= -1 AND score <= 1),
        confidence DOUBLE NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        evidence_ids_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        shadow_mode BOOLEAN NOT NULL,
        metadata_json JSON NOT NULL,
        UNIQUE (
            factor_type,
            symbol,
            data_cutoff,
            algorithm_version,
            input_snapshot_hash,
            shadow_mode
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_factor_outputs (
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        factor_id VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (decision_id, decision_version, factor_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_market_symbol_event_time
    ON canonical_market_records (symbol, event_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_market_content_hash
    ON canonical_market_records (content_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_financial_symbol_event_time
    ON canonical_financial_records (symbol, event_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_financial_content_hash
    ON canonical_financial_records (content_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_event_clusters_event_time
    ON event_clusters (event_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_event_source_cluster
    ON event_source_links (event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_event_symbol_cluster
    ON event_symbol_links (event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_event_sector_cluster
    ON event_sector_links (event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_factor_type_symbol_cutoff
    ON factor_outputs (factor_type, symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_decision_factor_output
    ON decision_factor_outputs (factor_id)
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_migration(connection: duckdb.DuckDBPyConnection) -> bool:
    """Apply this migration atomically.

    Returns True only when the migration is applied by this call.
    """

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
