from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest
from fastapi.testclient import TestClient

from database.connection_manager import DatabaseBusyError
from router.api.app import app
from trading.research.stock_report_performance import StockReportPerformance
import trading.research.stock_report_service as module


@pytest.mark.parametrize("error, expected_status", [
    (DatabaseBusyError("private path"), 503), (RuntimeError("programming error"), 500),
    (ValueError("invalid input"), 422),
])
def test_report_only_classifies_database_contention_as_retryable(monkeypatch, error, expected_status):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(module.StockReportService, "run", fail)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/research/stock-report", json={"symbol": "600172", "no_fetch": True, "no_ai": True},
    )
    assert response.status_code == expected_status
    if expected_status == 503:
        assert response.headers["Retry-After"] == "3"
        assert response.json()["error_code"] == "DATABASE_BUSY"
        assert "private path" not in response.text
    else:
        assert "Retry-After" not in response.headers


def test_failed_report_logs_time_and_outcome_without_error_contents(monkeypatch):
    import trading.research.stock_report_performance as performance_module
    records = []
    monkeypatch.setattr(performance_module, "_write_record", records.append)
    def fail(*args, **kwargs):
        raise DatabaseBusyError("DEEPSEEK_API_KEY=sensitive-error-message")
    monkeypatch.setattr(module.StockReportService, "_load_inputs", fail)
    with pytest.raises(DatabaseBusyError):
        module.StockReportService().run(symbol="600172", no_fetch=True, no_ai=True)
    assert len(records) == 1
    assert records[0]["success"] is False
    assert records[0]["error_type"] == "DatabaseBusyError"
    assert datetime.fromisoformat(records[0]["timestamp"]).utcoffset() is not None
    assert "sensitive-error-message" not in str(records)


@pytest.mark.parametrize("database_timezone", ["UTC", "Asia/Tokyo", "America/Los_Angeles"])
@pytest.mark.parametrize("hour, minute, expected_day", [(15, 59, 28), (16, 0, 29)])
def test_report_freshness_uses_shanghai_market_close(monkeypatch, database_timezone, hour, minute, expected_day):
    def database(*args, **kwargs):
        connection = duckdb.connect(":memory:")
        connection.execute(f"SET TimeZone = '{database_timezone}'")
        connection.execute("CREATE TABLE trading_calendar_days(calendar_date DATE, is_trading_day BOOLEAN, data_cutoff TIMESTAMPTZ)")
        connection.execute("INSERT INTO trading_calendar_days VALUES ('2026-07-28', TRUE, '2026-07-28T00:00:00Z'), ('2026-07-29', TRUE, '2026-07-28T00:00:00Z')")
        connection.execute("CREATE TABLE market_snapshot_items(snapshot_id VARCHAR, symbol VARCHAR, payload_json JSON)")
        connection.execute("CREATE TABLE market_snapshot_runs(snapshot_id VARCHAR, snapshot_time TIMESTAMPTZ)")
        return connection
    monkeypatch.setattr(module, "get_connection", database)
    for loader in ("load_bars", "load_financials", "load_news", "load_announcements"):
        monkeypatch.setattr(module, loader, lambda *args, **kwargs: [])
    performance = StockReportPerformance("test-cutoff", "600172", "RESEARCH", True, 0)
    cutoff = datetime(2026, 7, 29, hour, minute, tzinfo=module.TZ).astimezone(timezone.utc)
    result = module.StockReportService()._load_inputs("600172", performance, cutoff)
    assert result[-1].day == expected_day


def test_report_rejects_a_naive_cutoff(monkeypatch):
    monkeypatch.setattr(module, "get_connection", lambda **kwargs: pytest.fail("unexpected database access"))
    performance = StockReportPerformance("test-cutoff", "600172", "RESEARCH", True, 0)
    with pytest.raises(ValueError, match="timezone"):
        module.StockReportService()._load_inputs("600172", performance, datetime(2026, 7, 29))


def test_diagnostic_write_failure_does_not_break_reporting(monkeypatch, tmp_path, caplog):
    import trading.research.stock_report_performance as performance_module
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_text("fixture", encoding="utf-8")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(performance_module, "_performance_log_path", lambda: blocked / "performance.jsonl")
    performance = StockReportPerformance("log-failure", "600172", "RESEARCH", True, 0)
    performance.record_failure(DatabaseBusyError("private-provider-error"))
    result = performance.finish()
    assert result["success"] is False and result["error_type"] == "DatabaseBusyError"
    assert "performance log unavailable" in caplog.text
    assert "private-provider-error" not in caplog.text
