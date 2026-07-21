from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from config.settings import settings
from database.db import get_connection, initialize_database, insert_market_record
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.market_fact_service import MarketFactService


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "facts.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", database_path)
    initialize_database()
    return database_path


def _store_record(
    *,
    source_name: str,
    source_level: SourceLevel,
    value: object,
    verified: bool = True,
) -> None:
    record = MarketRecord(
        symbol="600172.SH",
        data_type=DataType.DAILY_BAR,
        event_time=datetime(2026, 7, 20, tzinfo=SHANGHAI_TZ),
        source_name=source_name,
        source_level=source_level,
        verified=verified,
        content_hash=f"{source_name}-{value}",
        data={"trade_date": "20260720", "close": value},
    )

    with get_connection() as connection:
        insert_market_record(connection, record)


def test_two_independent_sources_verify_numeric_fact(isolated_database) -> None:
    _store_record(
        source_name="Tushare Pro",
        source_level=SourceLevel.STRUCTURED,
        value=10.70,
    )
    _store_record(
        source_name="BaoStock",
        source_level=SourceLevel.STRUCTURED,
        value="10.70",
    )

    result = MarketFactService().verify_market_fact(
        symbol="600172",
        data_type="daily_bar",
        field="close",
        expected_value=10.70,
        event_date="2026-07-20",
    )

    assert result.verdict == "verified"
    assert result.symbol == "600172.SH"
    assert result.matched_sources == ["BaoStock", "Tushare Pro"]
    assert len(result.evidence) == 2


def test_single_non_official_source_is_insufficient(isolated_database) -> None:
    _store_record(
        source_name="Tushare Pro",
        source_level=SourceLevel.STRUCTURED,
        value=10.70,
    )

    result = MarketFactService().verify_market_fact(
        symbol="600172.SH",
        data_type="daily_bar",
        field="close",
        expected_value=10.70,
    )

    assert result.verdict == "insufficient_evidence"
    assert result.confidence == 0.3


def test_conflicting_value_is_reported(isolated_database) -> None:
    _store_record(
        source_name="BaoStock",
        source_level=SourceLevel.STRUCTURED,
        value=9.50,
    )

    result = MarketFactService().verify_market_fact(
        symbol="600172.SH",
        data_type="daily_bar",
        field="close",
        expected_value=10.70,
    )

    assert result.verdict == "conflicting"
    assert result.conflicting_sources == ["BaoStock"]


def test_rejects_unsafe_or_unknown_inputs(isolated_database) -> None:
    service = MarketFactService()

    with pytest.raises(ValueError, match="Unsupported data_type"):
        service.verify_market_fact("600172", "unknown", "close", 10)

    with pytest.raises(ValueError, match="dot-separated"):
        service.verify_market_fact("600172", "daily_bar", "close[$]", 10)
