from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.research.sentiment.v3.calculators import SnapshotBuildContext
from trading.research.sentiment.v3.capital import (
    CapitalCollector,
    CapitalRawData,
    CapitalStockRow,
    DragonTigerCollector,
    DragonTigerEvent,
    DragonTigerRawData,
    MarginRawData,
    MarginRow,
    calculate_capital_snapshots,
    calculate_dragon_tiger_snapshots,
    calculate_margin_snapshots,
)
from trading.research.sentiment.v3.capital_sources import _sanitize
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    CapitalMethodologyClass,
    Finality,
    MarketUniverse,
    QualityFlag,
    RecordSource,
    SectorMembership,
    SectorType,
    SnapshotType,
    UniverseScope,
)

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 12)
SOURCE = datetime(2026, 8, 12, 15, 0, tzinfo=TZ)
AVAILABLE = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)
NOW = datetime(2026, 8, 13, 9, 1, tzinfo=TZ)
SYMBOLS = ["000001.SZ", "600001.SH", "920001.BJ"]


def universe():
    return MarketUniverse(
        universe_id="u1",
        universe_version="A_SHARE_BASE_V1_TEST",
        universe_scope=UniverseScope.BASE_A_SHARE,
        trade_date=DAY,
        as_of=NOW,
        expected_symbol_count=3,
        eligible_symbol_count=3,
        observed_symbol_count=3,
        excluded_symbol_count=0,
        coverage_ratio=1,
        source="TEST",
        rules_version="A_SHARE_UNIVERSE_RULES_V1",
        symbols=SYMBOLS,
        symbol_hash="a" * 64,
        created_at=NOW,
    )


def context(available=AVAILABLE, record_source=RecordSource.TEST):
    return SnapshotBuildContext(
        trade_date=DAY,
        source="TEST",
        source_timestamp=SOURCE,
        available_time=available,
        data_cutoff=NOW,
        snapshot_time=NOW,
        finality=Finality.FINAL,
        record_source=record_source,
        data_version="v1",
        created_at=NOW,
    )


def memberships():
    return [
        SectorMembership(
            symbol=s,
            sector_id="IND:A" if s != "920001.BJ" else "IND:B",
            sector_name="A" if s != "920001.BJ" else "B",
            sector_type=SectorType.INDUSTRY,
            available_time=AVAILABLE,
            source="TEST current",
            membership_version="CURRENT_V1",
            pit_safe_for_history=False,
        )
        for s in SYMBOLS
    ]


def capital_raw(compatible=True):
    return CapitalRawData(
        source="TEST",
        source_timestamp=SOURCE,
        available_time=AVAILABLE,
        rows=[
            CapitalStockRow("000001.SZ", 1000, 800, 0.02, 10, 20, 30, 40, 100),
            CapitalStockRow("600001.SH", 2000, 2500, 0.03, -10, -20, -30, -40, -100),
            CapitalStockRow("920001.BJ", 3000, 3000, 0.04, 0, 0, 0, 0, 0),
        ],
        vendor_total_buy_amount=500,
        vendor_total_sell_amount=500,
        field_provenance={"vendor_flow": "TEST vendor"},
        methodology_compatible=compatible,
    )


def test_taxonomy_and_main_flow_is_vendor_not_institution():
    market, _, stocks = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )
    assert (
        market.field_methodology["market_turnover"].methodology_class
        == CapitalMethodologyClass.OBSERVABLE_FACT
    )
    assert (
        stocks[0].field_methodology["vendor_main_net_flow"].methodology_class
        == CapitalMethodologyClass.VENDOR_DERIVED
    )
    assert not hasattr(stocks[0], "institution_buying")


def test_transparent_derived_and_units():
    market, sectors, stocks = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )
    assert market.market_turnover == 6000
    assert market.field_methodology["market_turnover"].unit == "CNY"
    assert stocks[0].vendor_main_net_flow == 70
    assert sectors[0].vendor_main_flow_sum == 0
    assert stocks[0].relative_market_flow is not None


def test_incompatible_methodology_disables_relative():
    _, sectors, stocks = calculate_capital_snapshots(
        context=context(), raw=capital_raw(False), universe=universe(), memberships=memberships()
    )
    assert sectors[0].relative_market_flow is None
    assert stocks[0].relative_market_flow is None
    assert QualityFlag.INCOMPATIBLE_METHODOLOGY in stocks[0].meta.quality_flags


def test_missing_capital_is_none_not_zero_and_collector_isolates_error():
    class Broken:
        name = "BROKEN"

        def fetch(self, _):
            raise TimeoutError("secret")

    (market, sectors, stocks), _ = CapitalCollector(Broken()).collect(
        trade_date=DAY, universe=universe()
    )
    assert market.market_turnover is None and market.vendor_net_flow is None
    assert not sectors and not stocks
    assert QualityFlag.SOURCE_UNAVAILABLE in market.meta.quality_flags


def test_zero_is_preserved_as_real_value():
    market, _, stocks = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )
    assert market.vendor_main_net_flow == 0
    assert stocks[2].vendor_main_net_flow == 0


def margin_raw():
    return MarginRawData(
        source="TEST margin",
        source_timestamp=SOURCE,
        available_time=NOW,
        rows=[
            MarginRow("000001.SZ", 1000, 100, 40, 20, 5, 2, 1020),
            MarginRow("600001.SH", 0, 0, 0, 0, 0, 0, 0),
        ],
        previous_rows=[MarginRow("000001.SZ", 900, 90, 30, 10, 4, 1, 910)],
        market_margin_total=5000,
    )


def test_margin_fields_formula_zero_and_units():
    items = calculate_margin_snapshots(context=context(NOW), raw=margin_raw(), universe=universe())
    first = items[0]
    zero = items[1]
    assert first.has_margin_data is True
    assert zero.has_margin_data is True
    assert items[2].has_margin_data is False
    assert first.meta.coverage_ratio == pytest.approx(2 / 3)
    assert first.margin_net_buy == 60 and first.margin_balance_change == 100
    assert first.margin_balance_change_pct == pytest.approx(100 / 900)
    assert (
        first.field_methodology["margin_net_buy"].methodology_class
        == CapitalMethodologyClass.DERIVED_TRANSPARENT
    )
    assert first.field_methodology["short_sell"].unit == "SHARES"
    assert zero.margin_balance == 0 and zero.margin_net_buy == 0


def test_margin_missing_remains_none_and_next_session_pit():
    raw = MarginRawData(
        source="FAIL",
        source_timestamp=SOURCE,
        available_time=NOW,
        rows=None,
        previous_rows=None,
        market_margin_total=None,
    )
    items = calculate_margin_snapshots(context=context(NOW), raw=raw, universe=universe())
    assert items[0].has_margin_data is None
    assert items[0].margin_balance is None
    assert QualityFlag.MISSING_MARGIN_DATA in items[0].meta.quality_flags
    assert QualityFlag.NEXT_SESSION_AVAILABLE in items[0].meta.quality_flags


def dragon_raw(success=True):
    event = DragonTigerEvent(
        "000001.SZ",
        ("reason",),
        100,
        60,
        40,
        30,
        10,
        20,
        5,
        1,
        ("institution-seat",),
        ("broker-seat",),
    )
    return DragonTigerRawData(
        source="TEST LHB",
        source_timestamp=SOURCE,
        available_time=AVAILABLE,
        provider_succeeded=success,
        events=[event] if success else [],
    )


def test_dragon_event_not_listed_and_provider_failure_are_distinct():
    ok = calculate_dragon_tiger_snapshots(context=context(), raw=dragon_raw(), universe=universe())
    failed = calculate_dragon_tiger_snapshots(
        context=context(), raw=dragon_raw(False), universe=universe()
    )
    assert ok[0].has_dragon_tiger is True and ok[0].institution_net_amount == 20
    assert ok[1].has_dragon_tiger is False and ok[1].buy_amount is None
    assert failed[1].has_dragon_tiger is None
    assert QualityFlag.SOURCE_UNAVAILABLE in failed[1].meta.quality_flags


def test_dragon_event_methodology_and_eod_pit():
    item = calculate_dragon_tiger_snapshots(
        context=context(), raw=dragon_raw(), universe=universe()
    )[0]
    assert (
        item.field_methodology["institution_buy_amount"].methodology_class
        == CapitalMethodologyClass.EVENT_CAPITAL
    )
    assert QualityFlag.EOD_ONLY in item.meta.quality_flags
    assert item.meta.available_time == AVAILABLE


def test_dragon_collector_provider_exception_does_not_crash():
    class Broken:
        name = "BROKEN"

        def fetch(self, _):
            raise ConnectionError("token=secret")

    items, _ = DragonTigerCollector(Broken()).collect(trade_date=DAY, universe=universe())
    assert len(items) == 3 and all(item.has_dragon_tiger is None for item in items)


def test_links_and_membership_pit_flags():
    market, sectors, stocks = calculate_capital_snapshots(
        context=context(),
        raw=capital_raw(),
        universe=universe(),
        memberships=memberships(),
        market_snapshot_id="market-s1",
        stock_snapshot_ids={"000001.SZ": "stock-s1"},
    )
    assert market.market_snapshot_id == "market-s1"
    assert sectors[0].market_capital_snapshot_id == market.meta.snapshot_id
    assert stocks[0].market_capital_snapshot_id == market.meta.snapshot_id
    assert stocks[0].sector_capital_snapshot_id == sectors[0].meta.snapshot_id
    assert stocks[0].stock_snapshot_id == "stock-s1"
    assert QualityFlag.PIT_UNSAFE_FOR_HISTORY in sectors[0].meta.quality_flags


def test_content_hash_stable_and_repository_asof(tmp_path: Path):
    a = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )[0]
    b = calculate_capital_snapshots(
        context=replace(
            context(),
            snapshot_time=datetime(2026, 8, 13, 10, 0, tzinfo=TZ),
            created_at=datetime(2026, 8, 13, 10, 0, tzinfo=TZ),
        ),
        raw=capital_raw(),
        universe=universe(),
        memberships=memberships(),
    )[0]
    assert a.meta.content_hash == b.meta.content_hash
    repo = SentimentV3Repository(tmp_path / "s2c.duckdb")
    saved = repo.save_snapshot(a)
    assert repo.save_snapshot(b).meta.snapshot_id == saved.meta.snapshot_id
    assert (
        repo.get_snapshot_as_of(
            snapshot_type=SnapshotType.MARKET_CAPITAL,
            cutoff=datetime(2026, 8, 12, 17, 0, tzinfo=TZ),
            record_source=RecordSource.TEST,
        )
        is None
    )
    got = repo.get_snapshot_as_of(
        snapshot_type=SnapshotType.MARKET_CAPITAL, cutoff=NOW, record_source=RecordSource.TEST
    )
    assert got and got.meta.snapshot_id == a.meta.snapshot_id


def test_corrected_revision_chain(tmp_path: Path):
    repo = SentimentV3Repository(tmp_path / "revision.duckdb")
    base = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )[0]
    repo.save_snapshot(base)
    corrected_context = replace(
        context(),
        finality=Finality.CORRECTED,
        revision=2,
        corrects_snapshot_id=base.meta.snapshot_id,
        data_version="v2",
    )
    changed = replace(capital_raw(), vendor_total_buy_amount=501)
    corrected = calculate_capital_snapshots(
        context=corrected_context, raw=changed, universe=universe(), memberships=memberships()
    )[0]
    saved = repo.save_snapshot(corrected)
    assert saved.meta.corrects_snapshot_id == base.meta.snapshot_id and saved.meta.revision == 2


def test_snapshot_types_formal_weight_and_record_source():
    market, _, stocks = calculate_capital_snapshots(
        context=context(), raw=capital_raw(), universe=universe(), memberships=memberships()
    )
    assert market.meta.snapshot_type == SnapshotType.MARKET_CAPITAL
    assert stocks[0].meta.snapshot_type == SnapshotType.STOCK_CAPITAL
    assert market.meta.formal_weight == 0 and market.meta.research_only is True
    assert market.meta.record_source == RecordSource.TEST
    assert "secret-value" not in _sanitize(RuntimeError("token=secret-value"))
