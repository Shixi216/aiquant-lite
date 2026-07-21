from __future__ import annotations

import importlib
import inspect
import json
from typing import Any

from data_hub.services.announcement_service import (
    AnnouncementService,
)
from database.db import get_connection


SYMBOL = "600172.SH"
TARGET_ANNOUNCEMENT_ID = "1225422790"


def shorten(
    value: Any,
    limit: int = 800,
) -> str:
    text = str(value)

    if len(text) > limit:
        return text[:limit] + "...<truncated>"

    return text


def decode_json_value(
    value: Any,
) -> Any:
    if isinstance(value, (dict, list)):
        return value

    if not isinstance(value, str):
        return value

    stripped = value.strip()

    if not stripped.startswith(("{", "[")):
        return value

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def find_target(
    value: Any,
    target: str,
    path: str = "$",
) -> list[str]:
    matches: list[str] = []

    if isinstance(value, dict):
        for key, child in value.items():
            matches.extend(
                find_target(
                    child,
                    target,
                    f"{path}.{key}",
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(
                find_target(
                    child,
                    target,
                    f"{path}[{index}]",
                )
            )

    elif target in str(value):
        matches.append(path)

    return matches


def print_source(
    title: str,
    value: Any,
) -> None:
    print(f"\n=== {title} ===")

    try:
        print(inspect.getsource(value).rstrip())
    except (OSError, TypeError) as exc:
        print(
            "SOURCE_ERROR="
            f"{type(exc).__name__}: {exc}"
        )


def inspect_database() -> None:
    connection = get_connection()

    try:
        schema_rows = connection.execute(
            "DESCRIBE data_records"
        ).fetchall()

        cursor = connection.execute(
            """
            SELECT *
            FROM data_records
            WHERE
                data_type = ?
                AND symbol = ?
            ORDER BY event_time DESC
            LIMIT 5
            """,
            [
                "announcement",
                SYMBOL,
            ],
        )

        column_names = [
            description[0]
            for description in cursor.description
        ]

        rows = [
            dict(zip(column_names, row, strict=True))
            for row in cursor.fetchall()
        ]

        print("\n=== DATA_RECORDS SCHEMA ===")

        for row in schema_rows:
            print(row)

        print("\n=== ANNOUNCEMENT ROWS ===")
        print(f"row_count={len(rows)}")

        for index, row in enumerate(rows):
            print(f"\n--- row {index} ---")

            for name in (
                "record_id",
                "symbol",
                "data_type",
                "event_time",
                "fetched_at",
                "source_name",
                "source_url",
                "source_level",
                "verified",
                "content_hash",
            ):
                if name in row:
                    print(
                        f"{name}="
                        f"{shorten(row[name])}"
                    )

            target_locations: list[str] = []

            for column_name, raw_value in row.items():
                decoded = decode_json_value(
                    raw_value
                )

                if isinstance(decoded, dict):
                    print(
                        f"{column_name}_json_keys="
                        f"{sorted(decoded.keys())}"
                    )

                locations = find_target(
                    decoded,
                    TARGET_ANNOUNCEMENT_ID,
                    path=f"$.{column_name}",
                )
                target_locations.extend(locations)

            print(
                "announcement_id_locations="
                f"{target_locations}"
            )

        if rows:
            record_id = str(rows[0]["record_id"])

            exact_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM data_records
                WHERE record_id = ?
                """,
                [record_id],
            ).fetchone()[0]

            print("\n=== EXACT RECORD LOOKUP ===")
            print(f"record_id={record_id}")
            print(f"match_count={exact_count}")

    finally:
        connection.close()


def main() -> None:
    print("Announcement record storage inspection")

    print_source(
        "AnnouncementService.get_announcements",
        AnnouncementService.get_announcements,
    )

    route_module = importlib.import_module(
        "data_hub.api.routes.announcements"
    )

    route_function = getattr(
        route_module,
        "get_announcements",
        None,
    )

    print_source(
        "announcement API route",
        route_function,
    )

    inspect_database()

    print(
        "\nAnnouncement record storage "
        "inspection passed"
    )


if __name__ == "__main__":
    main()