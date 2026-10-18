from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import data_hub.repositories.history as history_module
import data_hub.services.market_fact_service as fact_module
from config.settings import settings
from database.db import get_connection, initialize_database, insert_market_record
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture(params=["UTC", "Asia/Tokyo", "America/Los_Angeles"])
def market_database(request, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "timezone.duckdb")
    initialize_database()

    def session_connection():
        connection = get_connection()
        connection.execute(f"SET TimeZone = '{request.param}'")
        return connection

    monkeypatch.setattr(history_module, "get_connection", session_connection)
    monkeypatch.setattr(fact_module, "get_connection", session_connection)
    return request.param


@pytest.mark.parametrize("hour, minute, expected_day", [(15, 59, 28), (16, 0, 29)])
def test_completed_trade_date_uses_shanghai_close(market_database, hour, minute, expected_day):
    repository = history_module.HistoryRepository()
    available_at = datetime(2026, 7, 28, tzinfo=SHANGHAI)
    repository.save_calendar(
        provider="TEST",
        rows=[
            {"calendar_date": date(2026, 7, day), "is_trading_day": True,
             "content_hash": f"calendar-{day}", "metadata": {}}
            for day in (28, 29)
        ],
        data_cutoff=available_at,
        fetched_at=available_at,
    )
    cutoff = datetime(2026, 7, 29, hour, minute, tzinfo=SHANGHAI)
    # The input and database may use different zones for the same instant.
    utc_cutoff = cutoff.astimezone(ZoneInfo("UTC"))
    assert repository.latest_completed_trade_date(utc_cutoff) == date(2026, 7, expected_day)


def test_fact_event_date_uses_shanghai_boundaries(market_database):
    with get_connection() as connection:
        for source, day, hour in (
            ("previous-day", 19, 23), ("opening-source", 20, 0),
            ("closing-source", 20, 23), ("next-day", 21, 0),
        ):
            insert_market_record(connection, MarketRecord(
                symbol="600172.SH", data_type=DataType.DAILY_BAR,
                event_time=datetime(2026, 7, day, hour, tzinfo=SHANGHAI),
                source_name=source, source_level=SourceLevel.STRUCTURED,
                verified=True, content_hash=source, data={"close": 10.7},
            ))
    result = fact_module.MarketFactService().verify_market_fact(
        symbol="600172.SH", data_type="daily_bar", field="close",
        expected_value=10.7, event_date="2026-07-20",
    )
    assert result.verdict == "verified"
    assert result.matched_sources == ["closing-source", "opening-source"]
    assert len(result.evidence) == 2


def test_completed_trade_date_rejects_naive_cutoff():
    with pytest.raises(ValueError, match="timezone"):
        history_module.HistoryRepository().latest_completed_trade_date(datetime(2026, 7, 29, 20))
