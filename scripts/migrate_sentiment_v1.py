from __future__ import annotations

import json

from database.db import get_connection
from database.migrations.v0103_sentiment_v1 import (
    MIGRATION_ID,
    apply_migration,
)


def main() -> int:
    with get_connection() as connection:
        before = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchone()[0]
        applied = apply_migration(connection)
        after = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "migration_id": MIGRATION_ID,
                "applied": applied,
                "table_count_before": before,
                "table_count_after": after,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
