from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from data_hub.services.full_market_common import normalize_symbol
from trading.research.sentiment.v3.adj_factor_batch_backfill import (
    BatchedAdjustmentFactorBackfill,
)
from trading.research.sentiment.v3.adj_factor_coverage import (
    BACKFILL_VERSION,
    AdjustmentFactorBackfill,
    AdjustmentFactorCoverageAuditor,
)
from trading.research.sentiment.v3.feature_registry import FEATURE_REGISTRY_V1
from trading.research.sentiment.v3.labels import (
    PRICE_ADJUSTMENT_VERSION,
    SUPPORTED_HORIZONS,
    label_specs_v1,
)


TZ = ZoneInfo("Asia/Shanghai")
DAYS = (date(2026, 7, 1), date(2026, 7, 2))


class FakeProvider:
    source = "Tushare Pro"

    def __init__(self, *, fail_once: date | None = None) -> None:
        self.calls: list[date] = []
        self.fail_once = fail_once

    def fetch_trade_date(self, trade_date: date):
        self.calls.append(trade_date)
        if self.fail_once == trade_date:
            self.fail_once = None
            raise ConnectionError("secret must not be persisted")
        return [
            {"ts_code": symbol, "trade_date": trade_date.strftime("%Y%m%d"), "adj_factor": factor}
            for symbol, factor in (
                ("600000.SH", 1.1),
                ("000001.SZ", 1.2),
                ("430001.BJ", 1.3),
                ("000002.SZ", 1.4),
                ("300001.SZ", 1.5),
            )
        ]


def create_database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE canonical_historical_bars (
            bar_id VARCHAR PRIMARY KEY, symbol VARCHAR, trade_date DATE,
            adjustment_type VARCHAR, verification_status VARCHAR,
            generated_at TIMESTAMPTZ
        );
        CREATE TABLE historical_adjustment_factors (
            factor_id VARCHAR PRIMARY KEY, symbol VARCHAR, trade_date DATE,
            adj_factor DOUBLE, event_time TIMESTAMPTZ,
            data_available_time TIMESTAMPTZ, data_cutoff TIMESTAMPTZ,
            fetched_at TIMESTAMPTZ, source VARCHAR, content_hash VARCHAR,
            raw_payload_json JSON,
            UNIQUE(symbol, trade_date, source, content_hash)
        );
        CREATE TABLE stock_universe (
            symbol VARCHAR, exchange VARCHAR, market VARCHAR, board VARCHAR,
            security_type VARCHAR, list_date DATE, delist_date DATE,
            listing_status VARCHAR, is_st BOOLEAN, universe_version VARCHAR
        );
        CREATE TABLE historical_security_statuses (
            symbol VARCHAR, status_type VARCHAR, status_value BOOLEAN,
            effective_start DATE, effective_end DATE
        );
        """
    )
    symbols = [
        ("600000.SH", "SH", "SH_MAIN", "ACTIVE", False, date(1999, 1, 1), None),
        ("000001.SZ", "SZ", "SZ_MAIN", "ACTIVE", True, date(1991, 1, 1), None),
        ("430001.BJ", "BJ", "BEIJING", "ACTIVE", False, date(2020, 1, 1), None),
        ("000002.SZ", "SZ", "SZ_MAIN", "DELISTED", False, date(1991, 1, 1), date(2026, 7, 3)),
        ("300001.SZ", "SZ", "CHINEXT", "ACTIVE", False, date(2026, 6, 1), None),
    ]
    connection.executemany(
        """
        INSERT INTO stock_universe
        VALUES (?, ?, 'A_SHARE', ?, 'STOCK', ?, ?, ?, ?, 'u1')
        """,
        [(s, e, b, ld, dd, st, is_st) for s, e, b, st, is_st, ld, dd in symbols],
    )
    rows = []
    for symbol, *_ in symbols:
        for day in DAYS:
            rows.append((f"{symbol}-{day}", symbol, day, "RAW", "VERIFIED", datetime.now(tz=TZ)))
    connection.executemany(
        "INSERT INTO canonical_historical_bars VALUES (?, ?, ?, ?, ?, ?)", rows
    )
    connection.execute(
        "INSERT INTO historical_security_statuses VALUES ('000001.SZ', 'ST', true, '2026-01-01', NULL)"
    )
    connection.close()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "s3a1.duckdb"
    create_database(path)
    return path


def factory(path: Path):
    return lambda: duckdb.connect(str(path))


def test_01_symbol_normalization_covers_sh_sz_bj():
    assert normalize_symbol("SH.600000") == "600000.SH"
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("430001") == "430001.BJ"


def test_02_audit_distinguishes_symbol_and_row_coverage(database: Path):
    report = AdjustmentFactorCoverageAuditor(factory(database)).run()
    assert report.overall.raw_bar_symbols == 5
    assert report.overall.raw_bar_rows == 10
    assert report.overall.matched_symbols == 0
    assert report.overall.matched_rows == 0


def test_03_exchange_matrix_keeps_bj(database: Path):
    report = AdjustmentFactorCoverageAuditor(factory(database)).run()
    values = {item.group: item.raw_bar_symbols for item in report.by_exchange}
    assert values == {"BJ": 1, "SH": 1, "SZ": 3}


def test_04_missing_factor_is_not_coalesced_to_one(database: Path):
    connection = duckdb.connect(str(database), read_only=True)
    value = connection.execute(
        """
        SELECT f.adj_factor FROM canonical_historical_bars b
        LEFT JOIN historical_adjustment_factors f USING(symbol, trade_date)
        LIMIT 1
        """
    ).fetchone()[0]
    connection.close()
    assert value is None


def test_05_backfill_normalizes_positive_factors_and_all_exchanges(database: Path):
    provider = FakeProvider()
    result = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    assert result.status == "COMPLETED"
    assert result.inserted_rows == 10
    connection = duckdb.connect(str(database), read_only=True)
    assert connection.execute("SELECT MIN(adj_factor) FROM historical_adjustment_factors").fetchone()[0] > 0
    assert {row[0] for row in connection.execute("SELECT DISTINCT split_part(symbol, '.', 2) FROM historical_adjustment_factors").fetchall()} == {"SH", "SZ", "BJ"}
    connection.close()


def test_06_backfill_is_idempotent(database: Path):
    provider = FakeProvider()
    service = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0,
    )
    first = service.run(start_date=DAYS[0], end_date=DAYS[-1])
    second = service.run(start_date=DAYS[0], end_date=DAYS[-1])
    assert first.inserted_rows == 10
    assert second.inserted_rows == 0
    assert len(provider.calls) == 2


def test_07_checkpoint_resume_continues_after_failure(database: Path):
    provider = FakeProvider(fail_once=DAYS[0])
    service = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0,
    )
    first = service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="resume")
    second = service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="resume")
    assert first.status == "FAILED"
    assert second.status == "COMPLETED"
    assert second.inserted_rows == 10


def test_08_partial_run_resumes_without_restarting(database: Path):
    provider = FakeProvider()
    service = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0,
    )
    first = service.run(
        start_date=DAYS[0], end_date=DAYS[-1], run_id="partial", max_dates=1
    )
    second = service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="partial")
    assert first.status == "PARTIAL"
    assert second.status == "COMPLETED"
    assert provider.calls == list(DAYS)


def test_09_active_filter_is_not_used_for_backfill(database: Path):
    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    connection = duckdb.connect(str(database), read_only=True)
    assert connection.execute("SELECT COUNT(*) FROM historical_adjustment_factors WHERE symbol='000002.SZ'").fetchone()[0] == 2
    connection.close()


def test_10_st_and_new_listing_are_not_filtered(database: Path):
    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    connection = duckdb.connect(str(database), read_only=True)
    values = dict(connection.execute(
        "SELECT symbol, COUNT(*) FROM historical_adjustment_factors WHERE symbol IN ('000001.SZ','300001.SZ') GROUP BY symbol"
    ).fetchall())
    connection.close()
    assert values == {"000001.SZ": 2, "300001.SZ": 2}


def test_11_provider_rows_outside_raw_universe_are_not_inserted(database: Path):
    class ExtraProvider(FakeProvider):
        def fetch_trade_date(self, trade_date: date):
            return super().fetch_trade_date(trade_date) + [{
                "ts_code": "600999.SH", "trade_date": trade_date.strftime("%Y%m%d"),
                "adj_factor": 9.0,
            }]

    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=ExtraProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    connection = duckdb.connect(str(database), read_only=True)
    assert connection.execute("SELECT COUNT(*) FROM historical_adjustment_factors WHERE symbol='600999.SH'").fetchone()[0] == 0
    connection.close()


def test_12_invalid_factor_is_rejected(database: Path):
    class InvalidProvider(FakeProvider):
        def fetch_trade_date(self, trade_date: date):
            rows = super().fetch_trade_date(trade_date)
            rows[0]["adj_factor"] = 0
            return rows

    result = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=InvalidProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    assert result.inserted_rows == 8


def test_13_checkpoint_error_is_sanitized(database: Path):
    service = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(fail_once=DAYS[0]),
        minimum_request_interval_seconds=0,
    )
    service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="safe")
    connection = duckdb.connect(str(database), read_only=True)
    error_type, message = connection.execute(
        "SELECT error_type, error_message_sanitized FROM sentiment_v3_adj_factor_backfill_items WHERE run_id='safe' AND status='FAILED'"
    ).fetchone()
    connection.close()
    assert error_type == "NETWORK_ERROR"
    assert message == "Tushare network request failed"


def test_14_content_hash_prevents_duplicates(database: Path):
    provider = FakeProvider()
    service = AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0,
    )
    service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="one")
    service.run(start_date=DAYS[0], end_date=DAYS[-1], run_id="two")
    connection = duckdb.connect(str(database), read_only=True)
    duplicate_count = connection.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT symbol || trade_date::VARCHAR || content_hash) FROM historical_adjustment_factors"
    ).fetchone()[0]
    connection.close()
    assert duplicate_count == 0


def test_15_audit_becomes_fully_covered_after_backfill(database: Path):
    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    report = AdjustmentFactorCoverageAuditor(factory(database)).run()
    assert report.overall.symbol_coverage_ratio == 1
    assert report.overall.row_coverage_ratio == 1


def test_16_trade_dates_are_real_dates(database: Path):
    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    connection = duckdb.connect(str(database), read_only=True)
    assert connection.execute("SELECT typeof(trade_date) FROM historical_adjustment_factors LIMIT 1").fetchone()[0] == "DATE"
    connection.close()


def test_17_backfill_metadata_records_version_and_source(database: Path):
    AdjustmentFactorBackfill(
        connection_factory=factory(database), provider=FakeProvider(),
        minimum_request_interval_seconds=0,
    ).run(start_date=DAYS[0], end_date=DAYS[-1])
    connection = duckdb.connect(str(database), read_only=True)
    version, source = connection.execute(
        "SELECT data_version, provider FROM sentiment_v3_adj_factor_backfill_runs"
    ).fetchone()
    connection.close()
    assert (version, source) == (BACKFILL_VERSION, "Tushare Pro")


def test_18_label_specs_and_adjustment_definition_remain_frozen():
    assert PRICE_ADJUSTMENT_VERSION == "ADJ_FACTOR_RETURN_V1"
    assert SUPPORTED_HORIZONS == (1, 3, 5, 10, 20)
    assert len(label_specs_v1()) == 10


def test_19_feature_registry_v1_remains_124_and_39():
    assert len(FEATURE_REGISTRY_V1.canonical_definitions()) == 124
    assert len(FEATURE_REGISTRY_V1.history_safe()) == 39


def test_20_s3a1_objects_expose_no_alpha_or_trade_action():
    forbidden = {"alpha", "weight", "buy", "sell", "hold", "action", "order"}
    fields = set(AdjustmentFactorCoverageAuditor.__dict__) | set(AdjustmentFactorBackfill.__dict__)
    assert not forbidden.intersection(fields)


def test_21_batched_writer_checkpoints_each_date(database: Path):
    provider = FakeProvider()
    result = BatchedAdjustmentFactorBackfill(
        connection_factory=factory(database), provider=provider,
        minimum_request_interval_seconds=0, database_batch_dates=2,
    ).run(start_date=DAYS[0], end_date=DAYS[-1], run_id="batch")
    assert result.status == "COMPLETED"
    assert result.inserted_rows == 10
    connection = duckdb.connect(str(database), read_only=True)
    statuses = connection.execute(
        "SELECT status, COUNT(*) FROM sentiment_v3_adj_factor_backfill_items "
        "WHERE run_id='batch' GROUP BY status"
    ).fetchall()
    run_totals = connection.execute(
        "SELECT api_calls, fetched_rows, normalized_rows, inserted_rows "
        "FROM sentiment_v3_adj_factor_backfill_runs WHERE run_id='batch'"
    ).fetchone()
    connection.close()
    assert statuses == [("SUCCESS", 2)]
    assert run_totals == (2, 10, 10, 10)
