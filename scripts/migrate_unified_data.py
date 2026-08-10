from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from config.settings import settings
from database.migrations.v0100_unified_data import (
    MIGRATION_ID,
    apply_migration,
)


def _tables(connection: duckdb.DuckDBPyConnection) -> list[str]:
    return sorted(row[0] for row in connection.execute("SHOW TABLES").fetchall())


def migrate(database_path: Path | None = None) -> dict[str, Any]:
    path = (database_path or settings.opc_database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as connection:
        before = _tables(connection)
        applied = apply_migration(connection)
        after = _tables(connection)
    return {
        "migration_id": MIGRATION_ID,
        "database_path": str(path),
        "applied": applied,
        "before_table_count": len(before),
        "after_table_count": len(after),
        "added_tables": sorted(set(after) - set(before)),
    }


def main() -> int:
    print(
        json.dumps(
            migrate(),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
