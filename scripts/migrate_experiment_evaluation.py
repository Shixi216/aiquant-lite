from __future__ import annotations

from database.db import get_connection
from database.migrations.v0111_experiment_evaluation_v1 import apply_migration


def main() -> None:
    with get_connection() as connection:
        applied = apply_migration(connection)
    print(f"0111_experiment_evaluation_v1 applied={str(applied).lower()}")


if __name__ == "__main__":
    main()
