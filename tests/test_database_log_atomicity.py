from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from database import connection_manager


def test_database_diagnostic_jsonl_is_atomic_across_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "database" / "atomic.duckdb"
    monkeypatch.setenv("HERMES_DB_DIAGNOSTICS", "1")

    def write(index: int) -> None:
        connection_manager._write_log(
            database,
            {
                "event": "atomic_probe",
                "index": index,
                "message": "中文 100% ≥ ± → ✅ ⚠️",
            },
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(write, range(200)))

    log = tmp_path / "logs" / "database-access.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 200
    assert {row["index"] for row in rows} == set(range(200))
    assert all(row["message"] == "中文 100% ≥ ± → ✅ ⚠️" for row in rows)
