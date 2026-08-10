from __future__ import annotations

import duckdb

from database.migrations.v0114_formal_history_validation import (
    MIGRATION_ID,
    apply_migration,
)


EXPECTED_TABLES = {
    "historical_adjustment_factors",
    "historical_security_statuses",
    "historical_benchmark_bars",
    "historical_industry_memberships",
    "historical_risk_events",
}


def test_formal_history_validation_migration_is_idempotent() -> None:
    connection = duckdb.connect(":memory:")
    assert apply_migration(connection) is True
    assert apply_migration(connection) is False
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert EXPECTED_TABLES <= tables
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
        [MIGRATION_ID],
    ).fetchone()[0] == 1


def test_all_research_tables_carry_point_in_time_fields() -> None:
    connection = duckdb.connect(":memory:")
    apply_migration(connection)
    for table in EXPECTED_TABLES:
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                """,
                [table],
            ).fetchall()
        }
        assert {"data_available_time", "data_cutoff"} <= columns