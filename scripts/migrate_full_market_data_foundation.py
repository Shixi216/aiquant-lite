from __future__ import annotations

import json

from database.db import get_connection
from database.migrations.v0106_full_market_data_foundation import (
    MIGRATION_ID,
    apply_migration,
)


def main() -> int:
    with get_connection() as connection:
        before = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchone()[0]
        )
        applied = apply_migration(connection)
        after = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchone()[0]
        )
    print(
        json.dumps(
            {
                "migration": MIGRATION_ID,
                "applied": applied,
                "table_count_before": before,
                "table_count_after": after,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
