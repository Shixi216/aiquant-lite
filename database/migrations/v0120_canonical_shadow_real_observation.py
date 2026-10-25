from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0120_canonical_shadow_real_observation"
SCHEMA_VERSION = "canonical-shadow-real-observation-v1"

MIGRATION_STATEMENTS = (
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS record_source VARCHAR DEFAULT 'SYNTHETIC'
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS legacy_technical_score DOUBLE
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS legacy_fundamental_score DOUBLE
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS canonical_technical_score DOUBLE
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS canonical_fundamental_score DOUBLE
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS shadow_overhead_ms DOUBLE DEFAULT 0
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS pit_diverged BOOLEAN DEFAULT FALSE
    """,
    """
    ALTER TABLE canonical_shadow_observations_v2
    ADD COLUMN IF NOT EXISTS canonical_error_type VARCHAR DEFAULT ''
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_shadow_v2_source_time
    ON canonical_shadow_observations_v2 (record_source, observed_at)
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
    # DuckDB cannot commit several ALTER TABLE operations on the same table
    # inside one transaction. Each statement is idempotent, so apply them
    # individually and record the migration only after all have succeeded.
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
