from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

_TABLE_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


def _coerce(text: str | None) -> Any:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if stripped == "True":
        return True
    if stripped == "False":
        return False
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return stripped


def load_csv_table(path: Path) -> dict[str, Any]:
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns)
    cached = _TABLE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = [
            {column: _coerce(value) for column, value in row.items()}
            for row in reader
        ]

    table = {"columns": columns, "rows": rows}
    _TABLE_CACHE.clear()
    _TABLE_CACHE[cache_key] = table
    return table
