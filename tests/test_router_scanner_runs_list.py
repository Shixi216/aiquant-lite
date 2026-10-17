from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb
from fastapi.testclient import TestClient

from config.settings import settings
from database.migrations.v0110_market_scanner_v1 import (
    apply_migration as apply_v0110,
)
from router.api.app import app

client = TestClient(app)


def _prepare(tmp_path, monkeypatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "scanner-runs.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    connection = duckdb.connect(str(db_path))
    apply_v0110(connection)
    return connection


def _insert_run(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    generated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO scanner_runs (
            run_id, query_id, analysis_mode, data_cutoff, snapshot_id,
            universe_count, scanned_count, matched_count, returned_count,
            parser_type, parser_version, scanner_version, plan_hash,
            input_snapshot_hash, database_query_count, network_request_count,
            model_call_count, elapsed_ms, peak_memory_bytes, status,
            risk_flags_json, payload_json, generated_at
        ) VALUES (
            ?, ?, 'SCREENING', ?, 'snap-1',
            5549, 5549, 12, 10,
            'LOCAL', 'v1', 'v1', ?, ?,
            3, 0, 0, 12.5, 1024, 'COMPLETED',
            '{}', '{}', ?
        )
        """,
        [
            run_id,
            f"sq_{run_id}",
            generated_at,
            f"planhash-{run_id}",
            f"snaphash-{run_id}",
            generated_at,
        ],
    )


def test_scanner_runs_list_sorted_and_limited(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    base = datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc)
    _insert_run(connection, "run-old", base)
    _insert_run(connection, "run-new", base + timedelta(hours=1))
    connection.close()

    response = client.get("/v1/scanner/runs")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["run_id"] for item in items] == ["run-new", "run-old"]
    assert items[0]["universe_count"] == 5549

    limited = client.get("/v1/scanner/runs", params={"limit": 1})
    assert [item["run_id"] for item in limited.json()["items"]] == ["run-new"]


def test_scanner_runs_list_empty_is_200(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    response = client.get("/v1/scanner/runs")
    assert response.status_code == 200
    assert response.json()["items"] == []
