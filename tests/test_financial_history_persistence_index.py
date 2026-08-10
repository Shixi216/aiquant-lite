import duckdb

from database.migrations.v0115_financial_history_persistence_index import (
    MIGRATION_ID,
    apply_migration,
)


def test_financial_history_content_hash_index_migration_is_idempotent() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE data_records (
            record_id VARCHAR PRIMARY KEY,
            content_hash VARCHAR
        )
        """
    )
    assert apply_migration(connection) is True
    assert apply_migration(connection) is False
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
        [MIGRATION_ID],
    ).fetchone()[0] == 1