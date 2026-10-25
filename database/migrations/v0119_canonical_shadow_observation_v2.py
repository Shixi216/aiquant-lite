from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0119_canonical_shadow_observation_v2"
SCHEMA_VERSION = "canonical-shadow-observation-v2"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS canonical_shadow_observations_v2 (
        observation_id VARCHAR PRIMARY KEY,
        request_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        local_user_id VARCHAR NOT NULL,
        external_user_id VARCHAR NOT NULL,
        channel VARCHAR NOT NULL,
        user_context_source VARCHAR NOT NULL,
        user_authenticated BOOLEAN NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        snapshot_id VARCHAR,
        legacy_score DOUBLE,
        canonical_score DOUBLE,
        legacy_action VARCHAR,
        canonical_action VARCHAR,
        legacy_veto BOOLEAN,
        canonical_veto BOOLEAN,
        canonical_pit BOOLEAN,
        canonical_data_status VARCHAR,
        score_source_status VARCHAR NOT NULL,
        veto_input_status VARCHAR NOT NULL,
        veto_source VARCHAR NOT NULL,
        veto_reason VARCHAR NOT NULL,
        veto_event_count BIGINT NOT NULL CHECK (veto_event_count >= 0),
        account_context_source VARCHAR NOT NULL,
        canonical_current_position DOUBLE,
        canonical_target_position DOUBLE,
        canonical_price_zones_json JSON NOT NULL,
        canonical_execution_status VARCHAR,
        latency_legacy_ms DOUBLE NOT NULL CHECK (latency_legacy_ms >= 0),
        latency_canonical_ms DOUBLE NOT NULL CHECK (
            latency_canonical_ms >= 0
        ),
        legacy_success BOOLEAN NOT NULL,
        canonical_success BOOLEAN NOT NULL,
        legacy_error VARCHAR NOT NULL,
        canonical_error VARCHAR NOT NULL,
        action_diverged BOOLEAN NOT NULL,
        veto_diverged BOOLEAN NOT NULL,
        score_diverged BOOLEAN NOT NULL,
        divergence_categories_json JSON NOT NULL,
        divergence_reason VARCHAR NOT NULL,
        score_bucket VARCHAR NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        affects_legacy_output BOOLEAN NOT NULL CHECK (
            affects_legacy_output = FALSE
        ),
        creates_order BOOLEAN NOT NULL CHECK (creates_order = FALSE),
        creates_trade BOOLEAN NOT NULL CHECK (creates_trade = FALSE),
        creates_trade_plan BOOLEAN NOT NULL CHECK (
            creates_trade_plan = FALSE
        ),
        creates_decision_packet BOOLEAN NOT NULL CHECK (
            creates_decision_packet = FALSE
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_shadow_v2_observed
    ON canonical_shadow_observations_v2 (observed_at, symbol)
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
            raise RuntimeError(f"migration checksum mismatch for {MIGRATION_ID}")
        return False
    connection.execute("BEGIN TRANSACTION")
    try:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            [MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now().astimezone()],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return True


__all__ = [
    "MIGRATION_CHECKSUM",
    "MIGRATION_ID",
    "SCHEMA_VERSION",
    "apply_migration",
]
