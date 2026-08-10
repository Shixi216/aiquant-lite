from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0116_overheat_preproduction_shadow"
SCHEMA_VERSION = "overheat-preproduction-shadow-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS overheat_shadow_observations (
        observation_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        source_bar_id VARCHAR,
        source_close DOUBLE,
        bar_history_count BIGINT NOT NULL,
        feature_status VARCHAR NOT NULL,
        original_technical_score DOUBLE NOT NULL,
        shadow_technical_score DOUBLE NOT NULL,
        ma20_ma60_expansion DOUBLE,
        ma60_slope_5 DOUBLE,
        expansion_penalty DOUBLE NOT NULL,
        ma60_slope_penalty DOUBLE NOT NULL,
        total_penalty DOUBLE NOT NULL,
        a_formal_score DOUBLE NOT NULL,
        b_shadow_formal_score DOUBLE NOT NULL,
        a_action VARCHAR NOT NULL,
        b_shadow_action VARCHAR NOT NULL,
        action_diverged BOOLEAN NOT NULL,
        hard_veto BOOLEAN NOT NULL,
        official_result_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        calibration_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        affects_production BOOLEAN NOT NULL CHECK (affects_production = FALSE),
        creates_trade_records BOOLEAN NOT NULL CHECK (creates_trade_records = FALSE)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_overheat_shadow_observation_cutoff
    ON overheat_shadow_observations (data_cutoff, symbol)
    """,
    """
    CREATE TABLE IF NOT EXISTS overheat_shadow_forward_labels (
        label_id VARCHAR PRIMARY KEY,
        observation_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        horizon_trading_days BIGINT NOT NULL
            CHECK (horizon_trading_days IN (1, 3, 5, 10, 20)),
        exit_trade_date DATE NOT NULL,
        exit_price DOUBLE NOT NULL,
        forward_return DOUBLE NOT NULL,
        maximum_favorable_excursion DOUBLE NOT NULL,
        maximum_adverse_excursion DOUBLE NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        calculated_at TIMESTAMPTZ NOT NULL,
        label_version VARCHAR NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        UNIQUE (observation_id, horizon_trading_days, label_version)
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
