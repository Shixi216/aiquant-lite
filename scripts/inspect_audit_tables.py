from __future__ import annotations

from database.db import get_connection


TABLES = [
    "tasks",
    "model_calls",
    "agent_results",
]


def main() -> None:
    connection = get_connection()

    try:
        for table in TABLES:
            print(f"\n=== {table} ===")

            rows = connection.execute(
                f"PRAGMA table_info('{table}')"
            ).fetchall()

            if not rows:
                print("table not found or has no columns")
                continue

            for row in rows:
                column_id = row[0]
                column_name = row[1]
                column_type = row[2]
                not_null = row[3]
                default_value = row[4]
                primary_key = row[5]

                print(
                    f"{column_id}: "
                    f"name={column_name}, "
                    f"type={column_type}, "
                    f"not_null={bool(not_null)}, "
                    f"default={default_value}, "
                    f"primary_key={bool(primary_key)}"
                )

            count = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

            print(f"row_count={count}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()