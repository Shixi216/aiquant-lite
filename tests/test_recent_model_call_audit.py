from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from database.connection_manager import DatabaseBusyError
from database.db import get_connection, initialize_database
from router.api.app import app
from router.services.audit_query import AuditQueryStore
from router.integration.sanitization import sanitize_error


START = datetime(2026, 10, 14, tzinfo=timezone.utc)
END = datetime(2026, 10, 16, tzinfo=timezone.utc)
SECRET = "legacy-sensitive-value"


@pytest.fixture(scope="module")
def audit_database(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("model-calls") / "calls.duckdb"
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "opc_database_path", path)
    initialize_database()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks VALUES ('task-linked', 'router_invoke', NULL, "
            "'completed', '{}', ?, ?)", [START, START],
        )
        for call_id, task_id, success, stamp in (
            ("call_a", "task-linked", True, "2026-10-15T00:00:00+00:00"),
            ("call_b", None, False, "2026-10-15T00:00:00+00:00"),
            ("call_c", "missing-task", True, "2026-10-15T00:00:00+00:00"),
            ("call_old", None, True, "2026-10-01T00:00:00+00:00"),
            ("call_future", None, True, "2026-10-20T00:00:00+00:00"),
        ):
            connection.execute(
                """
                INSERT INTO model_calls
                (call_id, task_id, agent_role, provider, model, input_tokens, output_tokens,
                 latency_ms, success, error_type, error_message, retry_count, created_at)
                VALUES (?, ?, 'sentiment', 'qwen', 'fixture-model', 2, 3, 10, ?, ?, ?, 0, ?)
                """,
                [call_id, task_id, success, None if success else "ProviderTimeout",
                 None if success else f"DEEPSEEK_API_KEY={SECRET} Bearer {SECRET}", stamp],
            )
    yield path
    patch.undo()


def parameters(**overrides):
    return {"since": START.isoformat(), "until": END.isoformat(), **overrides}


def test_calls_without_tasks_and_dangling_task_ids_are_visible(audit_database):
    client = TestClient(app)
    response = client.get("/v1/audit/model-calls", params=parameters())
    assert response.status_code == 200
    data = response.json()
    assert [item["call_id"] for item in data["calls"]] == ["call_c", "call_b", "call_a"]
    assert (data["total_count"], data["success_count"], data["failed_count"]) == (3, 2, 1)
    assert data["unlinked_call_count"] == 2
    assert data["count"] == 3 and data["invalid_record_count"] == 0
    task_list = client.get("/v1/audit/tasks?limit=100").json()
    assert task_list["count"] == 1 and task_list["tasks"][0]["model_call_count"] == 1


def test_limit_only_limits_rows_and_keeps_full_window_counts(audit_database):
    response = TestClient(app).get("/v1/audit/model-calls", params=parameters(limit=1))
    data = response.json()
    assert data["count"] == 1 and data["total_count"] == 3
    assert data["calls"][0]["call_id"] == "call_c"
    assert data["success_count"] == 2 and data["failed_count"] == 1


def test_legacy_errors_are_redacted_without_rewriting_stored_audit(audit_database):
    response = TestClient(app).get("/v1/audit/model-calls", params=parameters())
    assert SECRET not in response.text
    assert "[REDACTED]" in response.text
    with get_connection() as connection:
        error = connection.execute("SELECT error_message FROM model_calls WHERE call_id='call_b'").fetchone()[0]
    assert SECRET in error


def test_empty_window_is_reported_explicitly(audit_database):
    result = AuditQueryStore().list_model_calls(
        since=datetime(2026, 10, 2, tzinfo=timezone.utc),
        until=datetime(2026, 10, 3, tzinfo=timezone.utc), limit=10,
    )
    assert result.count == result.total_count == result.unlinked_call_count == 0
    assert result.success_count == result.failed_count == 0
    assert result.calls == []


@pytest.mark.parametrize("overrides", [
    {"since": "2026-10-14T00:00:00"}, {"until": "2026-10-16T00:00:00"},
    {"since": "2026-10-17T00:00:00Z"}, {"limit": 0}, {"limit": 1001},
])
def test_invalid_window_or_limit_fails_before_reading_database(monkeypatch, overrides):
    monkeypatch.setattr(AuditQueryStore, "_connect", lambda _: pytest.fail("unexpected database read"))
    response = TestClient(app).get("/v1/audit/model-calls", params=parameters(**overrides))
    assert response.status_code == 422


def test_equivalent_timezone_filters_select_the_same_instant(audit_database):
    response = TestClient(app).get("/v1/audit/model-calls", params={
        "since": "2026-10-15T09:00:00+09:00", "until": "2026-10-15T00:00:00Z",
    })
    assert response.status_code == 200
    assert response.json()["total_count"] == 3


def test_default_window_is_bounded_and_timezone_aware(audit_database, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(import_module("router.api.app"), "datetime", FrozenDatetime)
    data = TestClient(app).get("/v1/audit/model-calls").json()
    start, end = datetime.fromisoformat(data["since"]), datetime.fromisoformat(data["until"])
    assert start.utcoffset() is not None and end.utcoffset() is not None
    assert (end - start).days == 7
    assert {item["call_id"] for item in data["calls"]} == {"call_a", "call_b", "call_c"}


def test_busy_audit_returns_retryable_503_without_internal_details(monkeypatch):
    def unavailable(*args, **kwargs):
        raise DatabaseBusyError(f"private database path {SECRET}")
    monkeypatch.setattr(AuditQueryStore, "list_model_calls", unavailable)
    response = TestClient(app).get("/v1/audit/model-calls", params=parameters())
    assert response.status_code == 503 and response.headers["Retry-After"] == "3"
    assert response.json()["error_code"] == "DATABASE_BUSY"
    assert SECRET not in response.text


def test_unavailable_audit_has_a_sanitized_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(AuditQueryStore, "list_model_calls", unavailable)
    response = TestClient(app).get("/v1/audit/model-calls", params=parameters())
    assert response.status_code == 503 and SECRET not in response.text


@pytest.mark.parametrize("message", [
    f"API_KEY={SECRET}", f"TOKEN={SECRET}", f'PASSWORD="{SECRET} with spaces"',
    f'{{"api_key":"{SECRET}"}}', f'{{"access_token": "{SECRET}"}}',
    f"{{'SECRET': '{SECRET}'}}",
])
def test_legacy_generic_and_json_credential_assignments_are_redacted(message):
    assert SECRET not in sanitize_error(message)
    assert "[REDACTED]" in sanitize_error(message)
