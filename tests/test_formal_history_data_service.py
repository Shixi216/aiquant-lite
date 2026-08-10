from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb

from data_hub.services.formal_history_data_service import (
    FormalHistoryDataRepository,
)
from database.migrations.v0114_formal_history_validation import apply_migration


TZ = ZoneInfo("Asia/Shanghai")
FETCHED = datetime(2026, 8, 7, 10, tzinfo=TZ)


def repository():
    connection = duckdb.connect(":memory:")
    apply_migration(connection)
    return connection, FormalHistoryDataRepository(
        connection_factory=lambda: nullcontext(connection)
    )


def test_repository_persists_pit_market_context_idempotently() -> None:
    connection, repo = repository()
    factors = [{"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 2.0}]
    assert repo.save_adjustment_factors(factors, fetched_at=FETCHED) == 1
    assert repo.save_adjustment_factors(factors, fetched_at=FETCHED) == 0
    assert repo.save_security_statuses(
        suspensions=[{"ts_code": "600000.SH", "suspend_date": "20250103", "resume_date": "20250106"}],
        name_changes=[{"ts_code": "000001.SZ", "name": "ST sample", "start_date": "20250101", "end_date": "20250201"}],
        fetched_at=FETCHED,
    ) == 2
    row = connection.execute(
        "SELECT trade_date, data_available_time, data_cutoff FROM historical_adjustment_factors"
    ).fetchone()
    assert str(row[0]) == "2025-01-02"
    assert row[1] == row[2]


def test_repository_pairs_suspend_and_resume_events() -> None:
    connection, repo = repository()
    assert repo.save_security_statuses(
        suspensions=[
            {"ts_code": "600000.SH", "trade_date": "20250102", "suspend_type": "S"},
            {"ts_code": "600000.SH", "trade_date": "20250106", "suspend_type": "R"},
        ],
        name_changes=[],
        fetched_at=FETCHED,
    ) == 1
    assert connection.execute(
        "SELECT effective_start, effective_end FROM historical_security_statuses"
    ).fetchone() == (
        __import__("datetime").date(2025, 1, 2),
        __import__("datetime").date(2025, 1, 6),
    )

def test_repository_persists_benchmark_and_membership_dates() -> None:
    connection, repo = repository()
    assert repo.save_benchmark_bars(
        [{"trade_date": "20250102", "open": 3900, "high": 4010, "low": 3890, "close": 4000}],
        benchmark_code="000300.SH",
        benchmark_name="CSI300",
        benchmark_type="CSI300",
        source="Tushare Pro",
        fetched_at=FETCHED,
    ) == 1
    assert repo.save_industry_memberships(
        [{"ts_code": "600000.SH", "l1_code": "801780.SI", "l1_name": "Bank", "in_date": "20200101", "out_date": None}],
        industry_name="Bank",
        fetched_at=FETCHED,
    ) == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM historical_benchmark_bars"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM historical_industry_memberships"
    ).fetchone()[0] == 1
def test_qfq_materialization_uses_latest_factor_as_base() -> None:
    connection, repo = repository()
    connection.execute(
        """
        CREATE TABLE canonical_historical_bars (
            bar_id VARCHAR PRIMARY KEY, symbol VARCHAR, trade_date DATE,
            event_time TIMESTAMPTZ, data_available_time TIMESTAMPTZ,
            data_cutoff TIMESTAMPTZ, generated_at TIMESTAMPTZ,
            adjustment_type VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, volume DOUBLE, amount DOUBLE, volume_unit VARCHAR,
            amount_unit VARCHAR, primary_source VARCHAR,
            source_record_ids_json JSON, verification_source_ids_json JSON,
            verification_status VARCHAR, confidence DOUBLE,
            content_hash VARCHAR, algorithm_version VARCHAR,
            raw_payload_json JSON
        )
        """
    )
    connection.execute(
        """
        INSERT INTO canonical_historical_bars VALUES
        ('raw1','600000.SH','2025-01-02',TIMESTAMPTZ '2025-01-02 15:00:00+08',TIMESTAMPTZ '2025-01-02 16:00:00+08',TIMESTAMPTZ '2025-01-02 16:00:00+08',TIMESTAMPTZ '2025-01-02 16:00:00+08','RAW',10,11,9,10,100,1000,'SHARES','CNY','Tushare','["raw1"]','[]','SINGLE_SOURCE',0.5,'h1','raw','{}'),
        ('raw2','600000.SH','2025-01-03',TIMESTAMPTZ '2025-01-03 15:00:00+08',TIMESTAMPTZ '2025-01-03 16:00:00+08',TIMESTAMPTZ '2025-01-03 16:00:00+08',TIMESTAMPTZ '2025-01-03 16:00:00+08','RAW',12,13,11,12,100,1200,'SHARES','CNY','Tushare','["raw2"]','[]','SINGLE_SOURCE',0.5,'h2','raw','{}')
        """
    )
    repo.save_adjustment_factors([
        {"ts_code":"600000.SH","trade_date":"20250102","adj_factor":1.0},
        {"ts_code":"600000.SH","trade_date":"20250103","adj_factor":2.0},
    ], fetched_at=FETCHED)
    assert repo.materialize_forward_adjusted_bars(
        symbols=("600000.SH",),
        start_date=__import__("datetime").date(2025,1,2),
        end_date=__import__("datetime").date(2025,1,3),
        generated_at=FETCHED,
    ) == 2
    closes = connection.execute(
        "SELECT close FROM canonical_historical_bars WHERE adjustment_type='FORWARD_ADJUSTED' ORDER BY trade_date"
    ).fetchall()
    assert closes == [(5.0,), (12.0,)]
def test_repository_persists_risk_events() -> None:
    from data_hub.schemas.market import DataType, MarketRecord, SourceLevel

    connection, repo = repository()
    record = MarketRecord(
        record_id="announcement-1",
        symbol="600000.SH",
        data_type=DataType.ANNOUNCEMENT,
        event_time=FETCHED,
        fetched_at=FETCHED,
        source_name="CNInfo",
        source_level=SourceLevel.OFFICIAL,
        verified=True,
        content_hash="a" * 64,
        data={"title": "risk notice"},
    )
    assert repo.save_risk_events([record], fetched_at=FETCHED) == 1
    assert repo.save_risk_events([record], fetched_at=FETCHED) == 0
    row = connection.execute(
        "SELECT title, data_available_time, data_cutoff FROM historical_risk_events"
    ).fetchone()
    assert row[0] == "risk notice"
    assert row[1] == row[2]