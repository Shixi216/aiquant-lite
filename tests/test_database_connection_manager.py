from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from database import connection_manager


def test_managed_connection_closes_and_logs_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "database" / "test.duckdb"
    monkeypatch.setenv("HERMES_DB_DIAGNOSTICS", "1")
    with connection_manager.connect_database(
        database,
        configured_path=database,
    ) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    log = tmp_path / "logs" / "database-access.jsonl"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "database_open",
        "database_close",
    ]
    assert events[-1]["operation_type"] == "SELECT"
    assert events[-1]["elapsed_ms"] >= 0


def test_only_lock_open_errors_are_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    real_connect = duckdb.connect

    def flaky_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("Could not set lock on file")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(connection_manager.duckdb, "connect", flaky_connect)
    monkeypatch.setattr(connection_manager.time, "sleep", lambda _: None)
    database = tmp_path / "retry.duckdb"
    with connection_manager.connect_database(
        database,
        configured_path=database,
    ) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    assert attempts == 3


def test_non_lock_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def invalid_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("schema error")

    monkeypatch.setattr(connection_manager.duckdb, "connect", invalid_connect)
    with pytest.raises(RuntimeError, match="schema error"):
        connection_manager.connect_database(
            tmp_path / "invalid.duckdb",
            configured_path=tmp_path / "invalid.duckdb",
        )
    assert attempts == 1


def test_main_database_guard_blocks_non_owner_online_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "main.duckdb"
    monkeypatch.setattr(connection_manager, "_is_pytest", lambda: False)
    monkeypatch.setenv("HERMES_DB_ENFORCE_OWNER", "1")
    monkeypatch.delenv("HERMES_DB_OWNER_PROCESS", raising=False)
    monkeypatch.delenv("HERMES_DB_MAINTENANCE", raising=False)
    with pytest.raises(connection_manager.DatabaseOwnershipError):
        connection_manager.connect_database(
            database,
            configured_path=database,
            read_only=True,
        )


def test_exhausted_lock_retries_raise_typed_busy_error(tmp_path, monkeypatch):
    attempts = 0

    def locked(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(connection_manager.duckdb, "connect", locked)
    monkeypatch.setattr(connection_manager.time, "sleep", lambda _: None)
    with pytest.raises(connection_manager.DatabaseBusyError) as error:
        connection_manager.connect_database(tmp_path / "busy.duckdb", configured_path=tmp_path / "busy.duckdb")
    assert attempts == 4
    assert isinstance(error.value.__cause__, duckdb.IOException)


def test_gate_timeout_is_also_a_typed_busy_error(tmp_path, monkeypatch):
    class BusyGate:
        def acquire(self, *, timeout):
            return False

    monkeypatch.setattr(connection_manager, "_connect_gate", lambda _: BusyGate())
    with pytest.raises(connection_manager.DatabaseBusyError, match="门锁"):
        connection_manager.connect_database(tmp_path / "gate.duckdb", configured_path=tmp_path / "gate.duckdb")

