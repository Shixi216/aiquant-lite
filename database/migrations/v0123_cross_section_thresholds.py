from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0123_cross_section_thresholds"
SCHEMA_VERSION = "cross-section-thresholds-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS cross_section_thresholds (
        data_cutoff TIMESTAMPTZ NOT NULL,
        p50 DOUBLE NOT NULL,
        p75 DOUBLE NOT NULL,
        p90 DOUBLE NOT NULL,
        p95 DOUBLE NOT NULL,
        strong_buy_q DOUBLE NOT NULL,
        buy_q DOUBLE NOT NULL,
        n_total BIGINT NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (data_cutoff)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cross_section_factor_snapshot_history (
        snapshot_id VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        symbol VARCHAR NOT NULL,
        tech_rank DOUBLE,
        fund_rank DOUBLE,
        sent_rank DOUBLE,
        margin_rank DOUBLE,
        composite_score DOUBLE,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (snapshot_id, symbol)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cs_history_symbol_cutoff
    ON cross_section_factor_snapshot_history (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cs_thresholds_generated
    ON cross_section_thresholds (generated_at)
    """,
)


def _checksum() -> str:
    payload = "\n".join(item.strip() for item in MIGRATION_STATEMENTS)
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
