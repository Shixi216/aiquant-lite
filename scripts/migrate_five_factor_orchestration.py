from __future__ import annotations

import json

import duckdb

from config.settings import settings
from database.migrations.v0109_five_factor_orchestration import (
    MIGRATION_ID,
    apply_migration,
)


def main() -> None:
    with duckdb.connect(str(settings.opc_database_path)) as connection:
        applied = apply_migration(connection)
    print(
        json.dumps(
            {
                "migration_id": MIGRATION_ID,
                "applied": applied,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
