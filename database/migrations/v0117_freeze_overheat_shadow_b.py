from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0117_freeze_overheat_shadow_b"
SCHEMA_VERSION = "overheat-shadow-b-freeze-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS overheat_shadow_versions (
        shadow_version VARCHAR PRIMARY KEY,
        parameter_hash VARCHAR NOT NULL UNIQUE,
        effective_at TIMESTAMPTZ NOT NULL,
        code_version VARCHAR NOT NULL,
        formula_json JSON NOT NULL,
        calibration_json JSON NOT NULL,
        frozen BOOLEAN NOT NULL CHECK (frozen = TRUE),
        minimum_observation_trade_days BIGINT NOT NULL CHECK (
            minimum_observation_trade_days = 20
        ),
        target_observation_trade_days BIGINT NOT NULL CHECK (
            target_observation_trade_days = 30
        ),
        created_at TIMESTAMPTZ NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        affects_production BOOLEAN NOT NULL CHECK (affects_production = FALSE)
    )
    """,
    """
    ALTER TABLE overheat_shadow_observations
    ADD COLUMN IF NOT EXISTS shadow_version VARCHAR
    """,
    """
    ALTER TABLE overheat_shadow_observations
    ADD COLUMN IF NOT EXISTS parameter_hash VARCHAR
    """,
    """
    ALTER TABLE overheat_shadow_observations
    ADD COLUMN IF NOT EXISTS shadow_code_version VARCHAR
    """,
    """
    INSERT INTO overheat_shadow_versions VALUES (
        'B-OVERHEAT-1.0.0',
        'b976fa673c49520732825787b5da52ec792d8792ac378549fcb7d683a837e1bb',
        TIMESTAMPTZ '2026-08-10 11:52:42+08:00',
        'overheat-preproduction-shadow-v1',
        '{"expansion_speed_penalized":false,"formula":"shadow_technical_score=original_technical_score-expansion_penalty-ma60_slope_penalty"}',
        '{"expansion":{"field":"ma20_ma60_expansion","median":0.13222514188749512,"q75":0.20292835976991452,"weight":0.07034588503355267},"ma60_slope":{"field":"ma60_slope_5","median":0.03427116087581861,"q75":0.048701541558149986,"weight":0.07974481983998552},"maximum_component_multiplier":2.0}',
        TRUE, 20, 30,
        TIMESTAMPTZ '2026-08-10 11:52:42+08:00',
        TRUE, FALSE
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
