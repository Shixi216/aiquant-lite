from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb


SCORE_TERMS = ("technical", "fundamental", "formal_score", "sentiment")
DATE_TERMS = (
    "trade_date",
    "signal_date",
    "data_cutoff",
    "decision_cutoff",
    "created_at",
)


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


def inspect_database(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.as_posix(),
        "exists": path.exists(),
    }
    if not path.exists():
        return result
    with duckdb.connect(str(path), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT table_name, column_name, data_type, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'main'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
        tables: dict[str, list[dict[str, Any]]] = {}
        for table, column, data_type, position in rows:
            tables.setdefault(str(table), []).append(
                {
                    "name": str(column),
                    "type": str(data_type),
                    "position": int(position),
                }
            )
        relevant: list[dict[str, Any]] = []
        for table, columns in tables.items():
            names = [item["name"] for item in columns]
            lowered = [name.lower() for name in names]
            score_columns = [
                name
                for name in names
                if any(term in name.lower() for term in SCORE_TERMS)
            ]
            if not score_columns:
                continue
            date_columns = [name for name in names if name.lower() in DATE_TERMS]
            item: dict[str, Any] = {
                "table": table,
                "score_columns": score_columns,
                "date_columns": date_columns,
                "columns": columns,
                "row_count": int(_scalar(connection, f'SELECT COUNT(*) FROM "{table}"')),
            }
            date_ranges: dict[str, dict[str, Any]] = {}
            for date_column in date_columns:
                minimum, maximum = connection.execute(
                    f'SELECT MIN("{date_column}"), MAX("{date_column}") '
                    f'FROM "{table}"'
                ).fetchone()
                date_ranges[date_column] = {
                    "min": None if minimum is None else str(minimum),
                    "max": None if maximum is None else str(maximum),
                }
            item["date_ranges"] = date_ranges
            item["has_symbol"] = "symbol" in lowered
            relevant.append(item)
        result["relevant_tables"] = relevant
        result["table_count"] = len(tables)
        for key_table in ("replay_feature_packets", "replay_labels_wide"):
            if key_table in tables:
                result[key_table] = {
                    "row_count": int(
                        _scalar(connection, f'SELECT COUNT(*) FROM "{key_table}"')
                    ),
                    "columns": tables[key_table],
                }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--main-db",
        type=Path,
        default=Path("database/hermes_opc.duckdb"),
    )
    parser.add_argument(
        "--sentiment-db",
        type=Path,
        default=Path("database/sentiment_v3_replay.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("Strategy_Lab_Data_Inventory.json"),
    )
    args = parser.parse_args()
    payload = {
        "schema_version": 1,
        "databases": [
            inspect_database(args.main_db),
            inspect_database(args.sentiment_db),
        ],
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
