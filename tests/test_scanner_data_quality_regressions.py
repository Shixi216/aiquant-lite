"""Cross-entry regressions using synthetic facts and an isolated database."""
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from config.settings import settings
from database.db import get_connection, initialize_database
from data_hub.providers.tushare_snapshot import TushareSnapshotProvider, TushareSnapshotResult
from data_hub.schemas.market import MarketRecord, DataType, SourceLevel
from scripts.stock_report import _sync_daily_bar
from tests.repair_support import seed_scanner_database
from trading.scanner.feature_loader import ScannerFeatureLoader
from trading.scanner.freshness_guard import ScannerFreshnessGuard, FreshnessCheckResult
from trading.scanner.repository import ScannerRepository
from trading.scanner.technical_features import compute_technical_features

TZ = timezone(timedelta(hours=8))


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    path = tmp_path / "scanner-quality.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def seed_snapshot(cutoff, *, status="COMPLETE", coverage=1):
    with get_connection() as connection:
        symbols = seed_scanner_database(connection, cutoff, size=20)
        collected = cutoff - timedelta(seconds=60)
        connection.execute(
            "UPDATE market_snapshot_runs SET snapshot_time=?, data_cutoff=?, completed_at=?, completeness_status=?, coverage_ratio=?",
            [collected, collected, collected, status, coverage],
        )
        connection.execute(
            "UPDATE market_snapshot_items SET snapshot_time=?, payload_json=json_merge_patch(payload_json, '{\"trade_date\":\"20261022\"}')",
            [collected],
        )
    return symbols


def offline_guard(monkeypatch, expected="20261022"):
    guard = ScannerFreshnessGuard()
    monkeypatch.setattr(guard, "_latest_trade_date", lambda *_: expected)
    # Never launch the real background refresh process from a regression test.
    monkeypatch.setattr(guard, "_refresh_and_recheck", lambda result, *_: result)
    return guard


@pytest.mark.parametrize("case", ["partial", "metadata_95", "rows_95", "outsider", "invalid_price", "no_universe"])
def test_saved_snapshot_rejects_incomplete_coverage(isolated_database, monkeypatch, case):
    cutoff = datetime(2026, 10, 23, 10, tzinfo=TZ)
    symbols = seed_snapshot(cutoff, status="PARTIAL" if case == "partial" else "COMPLETE",
                            coverage=.95 if case in {"partial", "metadata_95"} else 1)
    with get_connection() as connection:
        if case in {"partial", "rows_95", "outsider"}:
            connection.execute("DELETE FROM market_snapshot_items WHERE symbol=?", [symbols[-1]])
        if case == "outsider":
            connection.execute("INSERT INTO market_snapshot_items SELECT * REPLACE ('999999.SH' AS symbol) FROM market_snapshot_items LIMIT 1")
        if case == "invalid_price":
            connection.execute("UPDATE market_snapshot_items SET price=0 WHERE symbol=?", [symbols[-1]])
        if case == "no_universe":
            connection.execute("UPDATE stock_universe SET listing_status='DELISTED'")
    result = offline_guard(monkeypatch).check(cutoff)
    assert not result.fresh
    assert "完整性" in result.reason or "覆盖" in result.reason


def test_complete_snapshot_with_legacy_ok_items_is_accepted(isolated_database, monkeypatch):
    cutoff = datetime(2026, 10, 23, 10, tzinfo=TZ)
    seed_snapshot(cutoff)
    with get_connection() as connection:
        connection.execute("UPDATE market_snapshot_items SET item_status='OK'")
    assert offline_guard(monkeypatch).check(cutoff).fresh


@pytest.mark.parametrize("cutoff,expected", [
    (datetime(2026, 10, 23, 9, tzinfo=TZ), "20261022"),
    (datetime(2026, 10, 23, 14, 59, tzinfo=TZ), "20261022"),
    (datetime(2026, 10, 23, 15, 1, tzinfo=TZ), "20261023"),
    (datetime(2026, 10, 23, 20, tzinfo=TZ), "20261023"),
    (datetime(2026, 10, 24, 10, tzinfo=TZ), "20261023"),
    (datetime(2026, 10, 26, 10, tzinfo=TZ), "20261023"),
    (datetime(2026, 10, 23, 2, tzinfo=timezone.utc), "20261022"),
])
def test_guard_and_local_scanner_agree_on_daily_target(isolated_database, monkeypatch, cutoff, expected):
    seed_snapshot(cutoff)
    known_at = datetime(2026, 10, 18, tzinfo=TZ)
    with get_connection() as connection:
        for number in range(22, 27):
            day = datetime(2026, 10, number, tzinfo=TZ).date()
            connection.execute("INSERT INTO trading_calendar_days VALUES ('TEST', ?, ?, ?, ?, ?, '{}')",
                               [day, day.weekday() < 5, known_at, known_at, f"calendar-{number}"])
        connection.execute("UPDATE market_snapshot_items SET payload_json=json_merge_patch(payload_json, ?)",
                           [json.dumps({"trade_date": expected})])
        if expected == "20261023" and cutoff.astimezone(TZ).date().day == 23:
            connection.execute("""INSERT INTO canonical_historical_bars
                SELECT * REPLACE ('today-' || bar_id AS bar_id, '2026-10-23'::DATE AS trade_date,
                    ?::TIMESTAMPTZ AS event_time, ?::TIMESTAMPTZ AS data_available_time,
                    ?::TIMESTAMPTZ AS data_cutoff, ?::TIMESTAMPTZ AS generated_at,
                    'today-' || content_hash AS content_hash)
                FROM canonical_historical_bars WHERE trade_date='2026-10-22'""", [cutoff] * 4)
    assert offline_guard(monkeypatch, expected).check(cutoff).fresh
    # Calendar network access is forbidden: the positive path must use local PIT facts.
    from data_hub.services.trading_calendar_service import TradingCalendarService
    monkeypatch.setattr(TradingCalendarService, "_fetch_calendar", lambda *args: pytest.fail("calendar network call"))
    features = ScannerFeatureLoader(ScannerRepository()).load(cutoff).frame
    assert not features.snapshot_stale.any()
    assert features.expected_trade_date.iloc[0].strftime("%Y%m%d") == expected


def test_sync_v3_revisions_do_not_duplicate_trading_days(isolated_database):
    cutoff = datetime.now(TZ) + timedelta(minutes=1)
    symbols = seed_snapshot(cutoff)
    repository = ScannerRepository()
    before = compute_technical_features(repository.load_feature_source(cutoff).frame).iloc[0]
    symbol = before.symbol
    assert symbol in symbols
    with get_connection() as connection:
        old = connection.execute("SELECT trade_date, close FROM canonical_historical_bars WHERE symbol=? ORDER BY trade_date", [symbol]).fetchall()
        connection.execute("UPDATE canonical_historical_bars SET algorithm_version='stock-report-sync-v2', volume=100000000 WHERE symbol=?", [symbol])
        for day, close in old:
            _sync_daily_bar(connection, MarketRecord(
                symbol=symbol, data_type=DataType.DAILY_BAR,
                event_time=datetime.combine(day, datetime.min.time(), tzinfo=TZ) + timedelta(hours=15),
                source_name="Tushare Pro", source_level=SourceLevel.STRUCTURED,
                content_hash=f"synthetic-{day}", data={"trade_date": day.strftime("%Y%m%d"),
                    "open": close, "high": close + .1, "low": close - .1, "close": close,
                    "vol": 10000, "volume": 1000000, "volume_unit": "SHARES",
                    "amount": 10000, "amount_unit": "THOUSAND_CNY"},
            ))
        assert connection.execute("SELECT COUNT(*) FROM canonical_historical_bars WHERE symbol=?", [symbol]).fetchone()[0] == 122
    after = compute_technical_features(repository.load_feature_source(cutoff).frame)
    row = after[after.symbol.eq(symbol)].iloc[0]
    assert row.history_count == len(set(row.history_dates)) == 61
    assert set(row.history_volumes) == {1000000}
    assert row.sma60 == pytest.approx(before.sma60)
    assert row.technical_score == pytest.approx(before.technical_score)


def test_future_revision_is_not_visible_at_earlier_cutoff(isolated_database):
    cutoff = datetime(2026, 10, 23, 10, tzinfo=TZ)
    symbols = seed_snapshot(cutoff)
    repository = ScannerRepository()
    before = repository.load_feature_source(cutoff).frame
    with get_connection() as connection:
        connection.execute("""INSERT INTO canonical_historical_bars SELECT * REPLACE (
            'future-revision' AS bar_id, 'future-hash' AS content_hash,
            ?::TIMESTAMPTZ AS generated_at, 999 AS close)
            FROM canonical_historical_bars WHERE symbol=? ORDER BY trade_date DESC LIMIT 1""",
            [cutoff + timedelta(hours=1), symbols[0]])
    after = repository.load_feature_source(cutoff).frame
    assert before.iloc[0].history_bar_ids.tolist() == after.iloc[0].history_bar_ids.tolist()
    assert 999 not in after.iloc[0].history_closes


def make_snapshot(symbols, cutoff):
    daily = [{"ts_code": code, "trade_date": "20261022", "close": 10, "open": 10,
              "high": 11, "low": 9, "pre_close": 10, "vol": 10000, "amount": 10000,
              "pct_chg": 0, "change": 0} for code in symbols]
    basic = [{"ts_code": code, "trade_date": "20261022", "turnover_rate": 5} for code in symbols]
    return TushareSnapshotResult(trade_date="20261022", snapshot_time=cutoff, collected_at=cutoff,
                                 daily=pd.DataFrame(daily), daily_basic=pd.DataFrame(basic))


@pytest.mark.parametrize("case", ["previous", "future", "mixed", "missing_date", "null_date", "empty", "duplicate", "moneyflow_date"])
def test_misaligned_source_dates_cannot_be_published(isolated_database, monkeypatch, case):
    cutoff = datetime(2026, 10, 23, 10, tzinfo=TZ)
    symbols = seed_snapshot(cutoff)
    snap = make_snapshot(symbols, cutoff)
    if case in {"previous", "future"}:
        snap.daily_basic["trade_date"] = "20261021" if case == "previous" else "20261023"
    elif case == "mixed":
        snap.daily_basic.loc[0, "trade_date"] = "20261021"
    elif case == "missing_date":
        snap.daily_basic = snap.daily_basic.drop(columns="trade_date")
    elif case == "null_date":
        snap.daily_basic.loc[0, "trade_date"] = None
    elif case == "empty":
        snap.daily_basic = pd.DataFrame()
    elif case == "duplicate":
        snap.daily_basic = pd.concat([snap.daily_basic, snap.daily_basic.iloc[:1]], ignore_index=True)
    else:
        snap.moneyflow = pd.DataFrame([{"ts_code": symbols[0], "trade_date": "20261021"}])
    provider = TushareSnapshotProvider.__new__(TushareSnapshotProvider)
    provider.settings = settings
    provider._validate(snap)
    assert not snap.trade_date_consistent
    guard = ScannerFreshnessGuard()
    with pytest.raises(ValueError):
        guard._write_tushare_snapshot(isolated_database, snap, {}, {})
    from data_hub.providers import tushare_snapshot
    monkeypatch.setattr(tushare_snapshot, "TushareSnapshotProvider", lambda: type("OfflineProvider", (), {"fetch_snapshot": lambda self: snap})())
    result = guard._do_refresh_inner(FreshnessCheckResult(fresh=False, latest_trade_date="20261022"), isolated_database)
    assert not result.fresh
    assert result.refresh_failed
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_snapshot_runs").fetchone()[0] == 1


def test_valid_snapshot_records_source_dates_and_unique_coverage(isolated_database):
    cutoff = datetime(2026, 10, 23, 10, tzinfo=TZ)
    symbols = seed_snapshot(cutoff)
    snap = make_snapshot(symbols, cutoff)
    provider = TushareSnapshotProvider.__new__(TushareSnapshotProvider)
    provider.settings = settings
    provider._validate(snap)
    assert snap.trade_date_consistent and snap.aligned and snap.coverage_ratio == 1
    sid = ScannerFreshnessGuard()._write_tushare_snapshot(isolated_database, snap, {}, {})
    with get_connection() as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM market_snapshot_items WHERE snapshot_id=? LIMIT 1", [sid]).fetchone()[0])
    assert payload["source_trade_dates"] == {"daily": "20261022", "daily_basic": "20261022"}
    snap.daily.loc[len(snap.daily) - 1, "ts_code"] = symbols[0]
    provider._validate(snap)
    assert snap.coverage_ratio == .95
    assert not snap.trade_date_consistent
