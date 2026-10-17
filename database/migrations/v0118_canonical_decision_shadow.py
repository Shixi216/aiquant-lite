from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0118_canonical_decision_shadow"
SCHEMA_VERSION = "canonical-decision-shadow-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS canonical_decision_shadow_audit (
        observation_id VARCHAR PRIMARY KEY,
        request_id VARCHAR NOT NULL,
        local_user_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        snapshot_id VARCHAR NOT NULL,
        legacy_formal_score DOUBLE NOT NULL,
        canonical_formal_score DOUBLE NOT NULL,
        legacy_action VARCHAR NOT NULL,
        canonical_action VARCHAR NOT NULL,
        legacy_veto BOOLEAN NOT NULL,
        canonical_veto BOOLEAN NOT NULL,
        veto_source VARCHAR NOT NULL,
        veto_reason VARCHAR NOT NULL,
        veto_input_complete BOOLEAN NOT NULL,
        canonical_data_status VARCHAR NOT NULL,
        canonical_point_in_time_ok BOOLEAN NOT NULL,
        account_context_source VARCHAR NOT NULL,
        account_context_is_research_assumption BOOLEAN NOT NULL,
        position_ratio_source VARCHAR NOT NULL,
        canonical_target_position DOUBLE NOT NULL,
        canonical_entry_zone_json JSON NOT NULL,
        canonical_preferred_zone_json JSON NOT NULL,
        canonical_stop_loss DOUBLE,
        canonical_take_profit_zone_json JSON NOT NULL,
        canonical_execution_status VARCHAR NOT NULL,
        action_diverged BOOLEAN NOT NULL,
        veto_diverged BOOLEAN NOT NULL,
        score_diverged BOOLEAN NOT NULL,
        score_source_mismatch BOOLEAN NOT NULL,
        divergence_reason VARCHAR NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        affects_legacy_output BOOLEAN NOT NULL CHECK (
            affects_legacy_output = FALSE
        ),
        creates_order BOOLEAN NOT NULL CHECK (creates_order = FALSE),
        creates_decision_packet BOOLEAN NOT NULL CHECK (
            creates_decision_packet = FALSE
        )
    )
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
