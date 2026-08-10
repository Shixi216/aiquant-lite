from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

from database import connection_manager


def test_lock_retry_records_wait_location_and_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "database" / "lock.duckdb"
    real_connect = duckdb.connect
    attempts = 0

    def flaky_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                f"Could not set lock on file; conflicting lock held by PID {os.getpid()}"
            )
        return real_connect(*args, **kwargs)

    monkeypatch.setenv("HERMES_DB_DIAGNOSTICS", "1")
    monkeypatch.setattr(connection_manager.duckdb, "connect", flaky_connect)
    monkeypatch.setattr(connection_manager.time, "sleep", lambda _: None)

    with connection_manager.connect_database(
        database,
        configured_path=database,
    ) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    log = tmp_path / "logs" / "database-access.jsonl"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    retry = next(event for event in events if event["event"] == "database_lock_retry")
    opened = next(event for event in events if event["event"] == "database_open")

    assert retry["lock_holder_pid"] == os.getpid()
    assert retry["lock_holder_process"]
    assert retry["lock_wait_location"]
    assert retry["waited_ms"] >= 0
    assert retry["duckdb_connect_ms"] >= 0
    assert opened["retry_count"] == 1
    assert opened["gate_wait_ms"] >= 0
