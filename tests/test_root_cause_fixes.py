from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import settings
from database.db import get_connection, initialize_database
from data_hub.market_units import daily_quantities
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.canonicalization_service import CanonicalizationService
from data_hub.services.trading_calendar_service import TradingCalendarService
from trading.scanner.query_parser import LocalChineseQueryParser
from trading.scanner.schemas import ScannerParseRequest
from trading.scanner.repository import ScannerRepository
from trading.scanner.freshness_guard import ScannerFreshnessGuard
from trading.scanner.universe_filter import _node_mask
from tests.repair_support import seed_scanner_database

TZ = timezone(timedelta(hours=8))


@pytest.mark.parametrize("data", [
    {"vol": 10, "volume_unit": "LOTS"},
    {"vol": 10, "volume": 1000, "volume_unit": "LOTS"},
    {"vol": 10, "volume": 1000, "volume_unit": "SHARES"},
    {"volume": 1000, "volume_unit": "SHARES"},
])
def test_tushare_legacy_and_current_units(data):
    assert daily_quantities({**data, "amount": 2, "amount_unit": "THOUSAND_CNY"}, "Tushare Pro") == (1000, 2000)
    assert daily_quantities({"volume": 1000, "volume_unit": "SHARES", "amount": 2000, "amount_unit": "CNY"}, "Tushare Pro") == (1000, 2000)


@pytest.mark.parametrize("query", ["成交额大于20亿元", "成交额大于1.5亿元", "换手率大于15%", "换手率大于1.5%", "价格大于20元", "价格大于1.5元"])
def test_complete_numeric_tokens_keep_units(query):
    response = LocalChineseQueryParser().parse(ScannerParseRequest(query=query, data_cutoff=datetime.now(TZ)))
    assert response.clarification_required is False


@pytest.mark.parametrize("hour, expected", [(9, "20261022"), (14, "20261022"), (15, "20261023"), (22, "20261023")])
def test_snapshot_request_target_is_not_completion_proof(monkeypatch, hour, expected):
    now = datetime(2026, 10, 23, hour, tzinfo=TZ)
    monkeypatch.setattr(TradingCalendarService, "_now", lambda self: now)
    monkeypatch.setattr(TradingCalendarService, "_fetch_calendar", lambda self, start, end: [
        {"cal_date": now.date() - timedelta(days=i), "is_open": True} for i in (0, 1, 2)
    ])
    calendar = TradingCalendarService()
    assert calendar.daily_snapshot_target_date(as_str=True) == expected
    assert calendar.latest_completed_trade_date()[0].strftime("%Y%m%d") == "20261022"
    assert ScannerFreshnessGuard()._latest_trade_date() == expected


@pytest.fixture
def repair_database(tmp_path, monkeypatch):
    path = tmp_path / "repair.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def test_provider_writer_sql_filter_contract(repair_database):
    now = datetime.now(TZ)
    with get_connection() as connection:
        seed_scanner_database(connection, now, size=2)
    snap = SimpleNamespace(trade_date="20261023", coverage_ratio=1, request_count=0, error="",
        daily=pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20261023", "close": 10, "open": 10, "high": 11, "low": 9,
                             "pre_close": 10, "vol": 50000, "amount": 200000, "pct_chg": 3, "change": .3}]),
        daily_basic=pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20261023", "turnover_rate": 4.6369}]),
        moneyflow=pd.DataFrame(), stk_limit=pd.DataFrame())
    ScannerFreshnessGuard()._write_tushare_snapshot(repair_database, snap, {}, {})
    frame = ScannerRepository().load_feature_source(datetime.now(TZ)).frame
    row = frame.loc[frame.symbol.eq("600000.SH")].iloc[0]
    assert row.current_volume == 5_000_000
    assert row.amount == 200_000_000
    assert row.turnover_rate == pytest.approx(.046369)
    assert row.change_pct == pytest.approx(.03)
    assert row.item_status == "VALID"
    for query, expected in [("成交额大于1亿元", True), ("换手率大于7%", False)]:
        plan = LocalChineseQueryParser().parse(ScannerParseRequest(query=query, data_cutoff=now)).parsed_query
        assert bool(_node_mask(frame, plan.filters[0])[0][frame.symbol.eq("600000.SH")].iloc[0]) is expected
    # Persisted snapshots lacking unit metadata remain readable in their native units.
    with get_connection() as connection:
        connection.execute("UPDATE market_snapshot_items SET payload_json='{}' WHERE source='Tushare'")
    legacy = ScannerRepository().load_feature_source(datetime.now(TZ)).frame
    assert legacy.loc[legacy.symbol.eq("600000.SH"), "amount"].iloc[0] == 200_000_000


def test_real_scanner_never_fetches_calendar_and_unknown_calendar_is_stale(repair_database, monkeypatch):
    from trading.scanner.feature_loader import ScannerFeatureLoader
    with get_connection() as connection:
        seed_scanner_database(connection, datetime.now(TZ), size=2)
    monkeypatch.setattr(TradingCalendarService, "_fetch_calendar", lambda *args: pytest.fail("scanner fetched network calendar"))
    ScannerFeatureLoader.clear_cache()
    result = ScannerFeatureLoader(ScannerRepository()).load(datetime.now(TZ))
    assert result.frame["snapshot_stale"].all()
    assert result.database_query_count == 1


def test_actual_provider_both_consumers_and_repeat_sync(repair_database):
    from data_hub.providers.tushare_provider import TushareProvider
    from scripts.stock_report import _sync_daily_bar
    provider = object.__new__(TushareProvider)
    provider._client = SimpleNamespace(daily=lambda **kwargs: pd.DataFrame([{
        "ts_code": "600000.SH", "trade_date": "20261023", "open": 10, "high": 11,
        "low": 9, "close": 10, "vol": 10, "amount": 2,
    }]))
    record = provider.get_daily_bars("600000.SH", "20261023", "20261023")[0]
    assert record.data["volume"] == 1000
    assert record.data["volume_unit"] == "SHARES"
    canonical = CanonicalizationService(initialize_repositories=False).canonicalize_market([record], persist=False)
    assert canonical.payload["volume"] == 1000
    assert canonical.payload["amount"] == 2000
    with get_connection() as connection:
        _sync_daily_bar(connection, record)
        _sync_daily_bar(connection, record)
        assert connection.execute("SELECT volume, amount FROM canonical_historical_bars").fetchall() == [(1000, 2000)]


def test_recent_collection_cannot_hide_old_or_missing_payload_date(repair_database, monkeypatch):
    now = datetime.now(TZ)
    with get_connection() as connection:
        seed_scanner_database(connection, now, size=2)
    guard = ScannerFreshnessGuard()
    expected = "20261023"
    monkeypatch.setattr(guard, "_latest_trade_date", lambda cutoff=None: expected)
    refreshes = []
    def blocked_refresh(result, path, force):
        refreshes.append(result.reason)
        return result
    monkeypatch.setattr(guard, "_refresh_and_recheck", blocked_refresh)
    for payload in ['{"trade_date":"20261022"}', '{}', '{"trade_date":"20261023"}']:
        with get_connection() as connection:
            connection.execute("UPDATE market_snapshot_items SET payload_json = ?", [payload])
        result = guard.check(now)
        assert result.fresh is ("20261023" in payload)
    assert len(refreshes) == 2


@pytest.mark.parametrize("coverage,aligned", [(0.90, True), (0.985, True), (1.0, False)])
def test_incomplete_refresh_fails_before_publish(repair_database, monkeypatch, coverage, aligned):
    from data_hub.providers import tushare_snapshot
    from trading.scanner.freshness_guard import FreshnessCheckResult
    snap = SimpleNamespace(error="", daily=pd.DataFrame([{"trade_date": "20261023"}]),
                           trade_date_consistent=True, coverage_ratio=coverage, aligned=aligned)
    monkeypatch.setattr(tushare_snapshot, "TushareSnapshotProvider", lambda: SimpleNamespace(fetch_snapshot=lambda: snap))
    guard = ScannerFreshnessGuard()
    monkeypatch.setattr(guard, "_write_tushare_snapshot", lambda *args: pytest.fail("incomplete snapshot published"))
    result = guard._do_refresh_inner(FreshnessCheckResult(fresh=False, latest_trade_date="20261023"), repair_database)
    assert result.fresh is False
    assert result.refresh_failed is True
    assert result.snapshot_id is None


def test_batch_persistence_is_atomic_and_idempotent(repair_database, monkeypatch):
    from data_hub.services.daily_bars_service import DailyBarsService
    from data_hub.repositories.unified import EvidenceNotFoundError
    from data_hub.repositories import CanonicalMarketRepository
    import data_hub.repositories.raw as raw_module
    from contextlib import contextmanager

    records = [MarketRecord(record_id=f"test-raw-{i}", symbol="600000.SH", data_type=DataType.DAILY_BAR,
        event_time=datetime(2026, 10, 1, 15, tzinfo=TZ) + timedelta(days=i),
        source_name="Tushare Pro", source_level=SourceLevel.STRUCTURED, content_hash=f"test-hash-{i}",
        data={"trade_date": f"202610{i+1:02d}", "close": 10, "vol": 10,
              "volume": 1000, "volume_unit": "SHARES", "amount": 100, "amount_unit": "THOUSAND_CNY"}) for i in range(10)]
    calls = []
    original = raw_module.get_connection
    @contextmanager
    def counted():
        calls.append(1)
        with original() as connection:
            yield connection
    monkeypatch.setattr(raw_module, "get_connection", counted)
    service = object.__new__(DailyBarsService)
    service._persist_records(records)
    assert len(calls) == 1  # one raw-resolution session for the entire window
    service._persist_records([r.model_copy(update={"record_id": f"refetched-{i}"}) for i, r in enumerate(records)])
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM canonical_market_records").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0] == 10
    repository = CanonicalMarketRepository()
    first = repository.list()[0]
    missing = first.model_copy(update={"canonical_record_id": "missing-ref", "source_record_ids": ["does-not-exist"]})
    with pytest.raises(EvidenceNotFoundError):
        repository.save_many([first, missing])
    assert len(repository.list()) == 10


def test_consistency_never_reads_active_database_file(repair_database, monkeypatch):
    from database import owner_routes
    monkeypatch.setattr(owner_routes, "_sha256", lambda path: pytest.fail("active database must not be hashed"))
    result = owner_routes.consistency()
    assert result["sha256"] is None
    assert result["sha256_status"] == "UNAVAILABLE_ACTIVE_DATABASE"
    assert result["duplicate_decision_packets"] == 0


@pytest.mark.parametrize("hard_veto", [False, True])
def test_empty_factor_evidence_degrades_without_inventing_action(repair_database, hard_veto):
    from router.integration.workflow import InteractionWorkflowService, new_request_id
    from router.integration.schemas import DecisionWorkflowRequest
    result = InteractionWorkflowService().decision(DecisionWorkflowRequest(
        symbol="603980.SH", data_cutoff=datetime.now(TZ), parent_request_id=new_request_id(),
        technical_score=1, technical_confidence=1, fundamental_score=1, fundamental_confidence=1,
        hard_veto=hard_veto, confirmation="CONFIRM_DECISION:603980.SH"), request_id=new_request_id())
    assert result.formal_result["status"] == "INSUFFICIENT_COVERAGE"
    assert result.formal_result["score"] is None
    assert result.formal_result["final_action"] == ("veto" if hard_veto else None)
    assert result.shadow_composite["status"] == "UNAVAILABLE"
    assert result.evidence_ids == []
    assert result.decision_packet_created is False


@pytest.mark.parametrize("budget, thinking", [(512, "disabled"), (1024, "disabled"), (4096, "enabled")])
def test_deepseek_respects_small_budget_without_reasoning_starvation(monkeypatch, budget, thinking):
    from router.providers import deepseek
    monkeypatch.setattr(deepseek, "router_settings", SimpleNamespace(deepseek_ready=True,
        deepseek_api_key="test-only", deepseek_base_url="https://example.invalid", deepseek_model="test"))
    async def capture(**kwargs):
        assert kwargs["max_tokens"] == budget
        assert kwargs["extra_body"]["thinking"]["type"] == thinking
        return kwargs
    monkeypatch.setattr(deepseek, "invoke_openai_compatible_chat", capture)
    asyncio.run(deepseek.DeepSeekProvider().invoke(role="risk_controller", prompt="test", system_prompt="test", temperature=0, max_tokens=budget))


def test_v2_revision_superseded_by_pit_visible_v3_on_read(repair_database):
    """复核2026-10-24场景1回归：库内已有v2旧修订后再同步v3，
    读取端必须按trade_date去重并选有效修订，而不是新旧混读61行31日。"""
    from scripts.stock_report import _sync_daily_bar
    from scripts.analyze_stock import load_bars

    cutoff = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)
    with get_connection() as connection:
        symbols = seed_scanner_database(connection, cutoff, size=3)
        symbol = symbols[0]
        # 模拟存量库：旧修订保留错误的成交量
        connection.execute(
            "UPDATE canonical_historical_bars SET volume=100000000, algorithm_version='stock-report-sync-v2' "
            "WHERE symbol=?",
            [symbol],
        )
        existing = connection.execute(
            "SELECT trade_date, open, high, low, close FROM canonical_historical_bars "
            "WHERE symbol=? ORDER BY trade_date",
            [symbol],
        ).fetchall()
        for day, op, hi, lo, close in existing:
            record = MarketRecord(
                symbol=symbol,
                data_type=DataType.DAILY_BAR,
                event_time=datetime.combine(day, datetime.min.time(), tzinfo=TZ) + timedelta(hours=15),
                source_name="Tushare Pro",
                source_level=SourceLevel.STRUCTURED,
                content_hash=f"v2-then-v3-{day}",
                data={
                    "trade_date": day.strftime("%Y%m%d"),
                    "open": op, "high": hi, "low": lo, "close": close,
                    "volume": 1000000, "vol": 10000, "volume_unit": "SHARES",
                    "amount": 10000, "amount_unit": "THOUSAND_CNY",
                },
            )
            _sync_daily_bar(connection, record)
        versions = dict(
            connection.execute(
                "SELECT algorithm_version, COUNT(*) FROM canonical_historical_bars "
                "WHERE symbol=? GROUP BY 1",
                [symbol],
            ).fetchall()
        )
        assert versions == {"stock-report-sync-v2": len(existing), "stock-report-sync-v3-units": len(existing)}

        # 阶段1：v3 生成于 cutoff 之后（PIT不可见）→ 扫描器读 v2，读取最新路径已见 v3
        frame = ScannerRepository().load_feature_source(cutoff).frame
        pit_row = frame[frame.symbol.eq(symbol)].iloc[0]
        assert int(pit_row.history_count) == len(set(pit_row.history_dates))
        assert 100000000 in pit_row.history_volumes
        latest_bars = load_bars(connection, symbol)
        assert len({b.trade_date for b in latest_bars}) == len(latest_bars)
        assert 100000000 not in [b.volume for b in latest_bars]

        # 阶段2：v3 前移到 cutoff 之前（PIT可见）→ 两个读取端都选 v3 新修订
        connection.execute(
            "UPDATE canonical_historical_bars SET generated_at=?, data_available_time=?, data_cutoff=? "
            "WHERE symbol=? AND algorithm_version='stock-report-sync-v3-units'",
            [cutoff - timedelta(hours=2), cutoff - timedelta(hours=2), cutoff - timedelta(hours=2), symbol],
        )
        frame = ScannerRepository().load_feature_source(cutoff).frame
        pit_row = frame[frame.symbol.eq(symbol)].iloc[0]
        assert int(pit_row.history_count) == len(set(pit_row.history_dates))
        assert 100000000 not in pit_row.history_volumes
        assert 1000000 in pit_row.history_volumes
