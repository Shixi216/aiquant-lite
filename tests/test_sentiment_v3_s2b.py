from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter, sleep
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data_hub.providers.tencent_snapshot import TencentSnapshotProvider
from trading.research.sentiment.v3.calculators import (
    MarketQuote,
    SnapshotBuildContext,
    SnapshotInputs,
    calculate_market_snapshot,
)
from trading.research.sentiment.v3.market_providers import (
    MarketBreadthProviderChain,
    TencentMarketBreadthProvider,
)
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    Finality,
    MarketUniverse,
    QualityFlag,
    RecordSource,
    SectorMembership,
    SectorType,
    SnapshotType,
    UniverseScope,
    UniverseSecurity,
)
from trading.research.sentiment.v3.sector_stock import (
    SectorStockRawData,
    SectorStockSentimentCollector,
    StockTradingBar,
    _suspended_symbols,
    calculate_sector_stock_snapshots,
)
from trading.research.sentiment.v3.universe import build_market_universe


TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 13)
SOURCE_TIME = datetime(2026, 8, 13, 15, 0, tzinfo=TZ)
AVAILABLE = datetime(2026, 8, 13, 16, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 8, 13, 16, 1, tzinfo=TZ)
NOW = datetime(2026, 8, 13, 16, 2, tzinfo=TZ)


def securities() -> list[UniverseSecurity]:
    return [
        UniverseSecurity(
            symbol="000001.SZ",
            exchange="SZ",
            board="SZ_MAIN",
            short_name="A",
            list_date=date(1991, 1, 1),
            listing_status="L",
        ),
        UniverseSecurity(
            symbol="600001.SH",
            exchange="SH",
            board="SH_MAIN",
            short_name="ST B",
            list_date=date(1992, 1, 1),
            listing_status="L",
            is_st=True,
        ),
        UniverseSecurity(
            symbol="300001.SZ",
            exchange="SZ",
            board="CHINEXT",
            short_name="C",
            list_date=DAY,
            listing_status="L",
            is_suspended=True,
        ),
        UniverseSecurity(
            symbol="920001.BJ",
            exchange="BJ",
            board="BEIJING",
            short_name="D",
            list_date=date(2024, 1, 1),
            listing_status="L",
        ),
        UniverseSecurity(
            symbol="510300.SH",
            exchange="SH",
            board="ETF",
            security_type="ETF",
            short_name="ETF",
            listing_status="L",
        ),
    ]


def memberships(*, safe: bool = False) -> list[SectorMembership]:
    result = []
    for symbol, sector in (("000001.SZ", "银行"), ("600001.SH", "制造"), ("920001.BJ", "制造")):
        result.append(
            SectorMembership(
                symbol=symbol,
                sector_id=f"IND:{sector}",
                sector_name=sector,
                sector_type=SectorType.INDUSTRY,
                available_time=AVAILABLE,
                source="TEST current membership",
                membership_version="CURRENT_V1",
                pit_safe_for_history=safe,
            )
        )
    return result


def universe(
    *, observed: list[str] | None = None, scope: UniverseScope = UniverseScope.BREADTH_ELIGIBLE
) -> MarketUniverse:
    return build_market_universe(
        trade_date=DAY,
        as_of=CUTOFF,
        securities=securities(),
        observed_symbols=observed or ["000001.SZ", "600001.SH", "920001.BJ"],
        source="TEST",
        source_universe_version="SRC_V1",
        scope=scope,
        memberships=memberships(),
        created_at=NOW,
    )


def context(record_source: RecordSource = RecordSource.TEST) -> SnapshotBuildContext:
    return SnapshotBuildContext(
        trade_date=DAY,
        source="TEST EOD",
        source_timestamp=SOURCE_TIME,
        available_time=AVAILABLE,
        data_cutoff=CUTOFF,
        snapshot_time=NOW,
        finality=Finality.FINAL,
        record_source=record_source,
        data_version="v1",
        created_at=NOW,
        quality_flags=(QualityFlag.EOD_ONLY,),
    )


def market():
    return calculate_market_snapshot(
        context(),
        SnapshotInputs(
            quotes=[
                MarketQuote("000001.SZ", 0.04, 100),
                MarketQuote("600001.SH", 0.07, 200),
                MarketQuote("920001.BJ", 0.02, 300),
            ],
            limit_up=[],
            limit_down_symbols=[],
            broken_board_symbols=["600001.SH"],
            strong_stock_symbols=[],
            expected_universe_size=3,
        ),
    )


def bars() -> list[StockTradingBar]:
    return [
        StockTradingBar("000001.SZ", 104, 100, 101, 106, 99, 120, 1000, 0.02, 1.2, 0.04),
        StockTradingBar("600001.SH", 107, 100, 102, 110, 101, 220, 2000, 0.03, 1.5, 0.07),
        StockTradingBar("920001.BJ", 102, 100, 99, 104, 98, 320, 3000, 0.04, 1.1, 0.02),
    ]


def previous_bars() -> list[StockTradingBar]:
    return [
        StockTradingBar("000001.SZ", 100, 99, 99, 101, 98, 100, 800),
        StockTradingBar("600001.SH", 100, 99, 99, 101, 98, 200, 1600),
        StockTradingBar("920001.BJ", 100, 99, 99, 101, 98, 300, 2400),
    ]


def calculated(*, market_value=True, safe=False):
    return calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=market() if market_value else None,
        memberships=memberships(safe=safe),
        bars=bars(),
        previous_bars=previous_bars(),
        field_provenance={"prices": "TEST"},
    )


def test_01_universe_version_and_hash_are_stable() -> None:
    assert universe() == universe()
    assert universe().universe_version.startswith("A_SHARE_BASE_V1_")


def test_02_universe_change_creates_new_version() -> None:
    changed = build_market_universe(
        trade_date=DAY,
        as_of=CUTOFF,
        securities=securities()[:-2],
        observed_symbols=["000001.SZ"],
        source="TEST",
        source_universe_version="SRC_V2",
    )
    assert changed.universe_version != universe().universe_version


def test_03_non_stock_is_excluded() -> None:
    value = universe()
    assert "510300.SH" not in value.symbols
    assert value.exclusion_summary["NON_A_SHARE_OR_NON_STOCK_INPUT"] == 1


def test_04_st_bj_and_new_stock_remain_in_base() -> None:
    value = universe(scope=UniverseScope.BASE_A_SHARE)
    assert {"600001.SH", "920001.BJ", "300001.SZ"} <= set(value.symbols)


def test_05_suspended_is_excluded_only_from_breadth() -> None:
    assert "300001.SZ" not in universe().symbols
    assert universe().exclusion_summary["CONFIRMED_SUSPENDED"] == 1


def test_06_expected_observed_coverage_are_explicit() -> None:
    value = universe(observed=["000001.SZ", "920001.BJ"])
    assert (
        value.expected_symbol_count,
        value.eligible_symbol_count,
        value.observed_symbol_count,
    ) == (4, 3, 2)
    assert value.coverage_ratio == pytest.approx(2 / 3)


def test_07_sector_eligible_excludes_missing_membership() -> None:
    value = universe(scope=UniverseScope.SECTOR_ELIGIBLE)
    assert value.eligible_symbol_count == 3


def test_08_repository_preserves_universe_versions(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    first = repo.save_universe(universe())
    second = first.model_copy(
        update={
            "universe_id": "u2",
            "universe_version": "V2",
            "as_of": CUTOFF + timedelta(minutes=1),
            "created_at": NOW + timedelta(minutes=1),
        }
    )
    repo.save_universe(second)
    assert repo.get_universe_as_of(cutoff=CUTOFF).universe_id == first.universe_id
    assert repo.get_universe_as_of(cutoff=CUTOFF + timedelta(minutes=2)).universe_id == "u2"


def test_09_provider_symbol_difference_is_visible_in_coverage() -> None:
    assert universe(observed=["000001.SZ"]).coverage_ratio == pytest.approx(1 / 3)


def test_10_tencent_total_budget_times_out_quickly(monkeypatch) -> None:
    monkeypatch.setattr(
        TencentSnapshotProvider, "_fetch_batch", lambda *args: (sleep(0.2), pd.DataFrame())[1]
    )
    started = perf_counter()
    with pytest.raises(TimeoutError, match="deadline"):
        TencentSnapshotProvider().fetch_market_snapshot(["000001"], total_timeout_seconds=0.02)
    assert perf_counter() - started < 0.15


def test_10b_tencent_adapter_budget_includes_universe_load(monkeypatch) -> None:
    provider = TencentMarketBreadthProvider(time_budget_seconds=0.01)

    def slow_symbols(deadline):
        del deadline
        sleep(0.02)
        return ["000001"]

    monkeypatch.setattr(provider, "_active_symbols", slow_symbols)
    with pytest.raises(TimeoutError, match="before quote"):
        provider.fetch(trade_date=DAY, data_cutoff=CUTOFF, finality=Finality.INTRADAY)


def test_11_tencent_succeeds_inside_budget(monkeypatch) -> None:
    frame = pd.DataFrame([{"symbol": "000001.SZ"}])
    monkeypatch.setattr(TencentSnapshotProvider, "_fetch_batch", lambda *args: frame)
    result = TencentSnapshotProvider().fetch_market_snapshot(["000001"], total_timeout_seconds=1)
    assert len(result) == 1


class TimeoutProvider:
    name = "timeout"

    def fetch(self, **kwargs):
        del kwargs
        raise TimeoutError("deadline exceeded")


def test_12_provider_timeout_quality_and_partial() -> None:
    result = MarketBreadthProviderChain([TimeoutProvider()]).resolve(
        trade_date=DAY, data_cutoff=CUTOFF, finality=Finality.INTRADAY
    )
    assert result.data is None
    assert {QualityFlag.PROVIDER_TIMEOUT, QualityFlag.PARTIAL_DATA} <= set(result.quality_flags)


def test_12b_resume_rows_are_not_marked_suspended() -> None:
    frame = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "suspend_type": "S"},
            {"ts_code": "000002.SZ", "suspend_type": "R"},
        ]
    )
    assert _suspended_symbols(frame) == {"000001.SZ"}


def test_13_sector_advancers_decliners_mean_median() -> None:
    sectors, _ = calculated()
    manufacturing = next(item for item in sectors if item.sector_name == "制造")
    assert (manufacturing.advancers, manufacturing.decliners) == (2, 0)
    assert manufacturing.mean_return == manufacturing.median_return == pytest.approx(0.045)


def test_14_sector_turnover_and_change() -> None:
    sectors, _ = calculated()
    manufacturing = next(item for item in sectors if item.sector_name == "制造")
    assert manufacturing.sector_turnover == 5000
    assert manufacturing.turnover_change == pytest.approx(0.25)


def test_15_sector_limit_and_broken_fields_preserve_zero_vs_missing() -> None:
    sectors, _ = calculated()
    manufacturing = next(item for item in sectors if item.sector_name == "制造")
    assert manufacturing.limit_up_count == 0
    assert manufacturing.broken_board_count == 1
    assert manufacturing.broken_board_rate == 1


def test_16_sector_coverage_and_member_missing() -> None:
    reduced = bars()[:-1]
    sectors, _ = calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=market(),
        memberships=memberships(),
        bars=reduced,
    )
    manufacturing = next(item for item in sectors if item.sector_name == "制造")
    assert manufacturing.meta.coverage_ratio == 0.5
    assert QualityFlag.PARTIAL_DATA in manufacturing.meta.quality_flags


def test_17_current_membership_is_history_unsafe() -> None:
    sectors, _ = calculated()
    assert QualityFlag.CURRENT_MEMBERSHIP_ONLY in sectors[0].meta.quality_flags
    assert QualityFlag.PIT_UNSAFE_FOR_HISTORY in sectors[0].meta.quality_flags


def test_18_historical_membership_can_be_marked_safe() -> None:
    sectors, _ = calculated(safe=True)
    assert QualityFlag.PIT_UNSAFE_FOR_HISTORY not in sectors[0].meta.quality_flags


def test_19_sector_relative_market_keeps_all_components() -> None:
    sectors, _ = calculated()
    bank = next(item for item in sectors if item.sector_name == "银行")
    assert bank.market_return == pytest.approx((0.04 + 0.07 + 0.02) / 3)
    assert bank.relative_market_return == pytest.approx(0.04 - bank.market_return)


def test_20_leader_is_current_return_rank_with_stable_tie_break() -> None:
    sectors, _ = calculated()
    manufacturing = next(item for item in sectors if item.sector_name == "制造")
    assert (manufacturing.leader_symbol, manufacturing.leader_return) == ("600001.SH", 0.07)
    assert manufacturing.leader_definition == "RETURN_RANK_V1"


def test_21_sector_missing_values_are_not_zero() -> None:
    incomplete_market = market().model_copy(
        update={"limit_up_symbols": None, "broken_board_symbols": None}
    )
    sectors, _ = calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=incomplete_market,
        memberships=memberships(),
        bars=bars(),
        previous_bars=previous_bars(),
    )
    assert sectors[0].limit_up_count is None and sectors[0].broken_board_count is None


def test_22_stock_return_amplitude_turnover_and_clv() -> None:
    _, stocks = calculated()
    stock = next(item for item in stocks if item.symbol == "000001.SZ")
    assert stock.return_pct == 0.04
    assert stock.amplitude == pytest.approx(0.07)
    assert stock.turnover_rate == 0.02
    assert stock.close_location_value == pytest.approx(5 / 7)


def test_23_clv_high_equals_low_is_missing_not_zero() -> None:
    flat = replace(bars()[0], high=100, low=100, close=100)
    _, stocks = calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=market(),
        memberships=memberships(),
        bars=[flat],
    )
    assert stocks[0].close_location_value is None
    assert "close_location_value" in stocks[0].meta.missing_fields


def test_24_gap_path_uses_no_magnitude_threshold() -> None:
    _, stocks = calculated()
    stock = next(item for item in stocks if item.symbol == "000001.SZ")
    assert stock.gap_and_follow_through > 0
    assert stock.gap_and_fade == 0
    assert stock.gap_path_version == "GAP_PATH_V1"


def test_25_stock_relative_market_and_sector() -> None:
    _, stocks = calculated()
    stock = next(item for item in stocks if item.symbol == "600001.SH")
    assert stock.relative_market_return == pytest.approx(stock.return_pct - stock.market_return)
    assert stock.relative_sector_return == pytest.approx(0.07 - 0.045)


def test_26_missing_market_keeps_relative_none() -> None:
    sectors, stocks = calculated(market_value=False)
    assert all(item.relative_market_return is None for item in sectors)
    assert all(item.relative_market_return is None for item in stocks)
    assert all(item.market_snapshot_id is None for item in stocks)


def test_27_missing_sector_keeps_relative_none() -> None:
    _, stocks = calculate_sector_stock_snapshots(
        context=context(), universe=universe(), market=market(), memberships=[], bars=bars()
    )
    assert all(item.relative_sector_return is None for item in stocks)
    assert all(item.sector_snapshot_id is None for item in stocks)


def test_28_limit_states_come_from_pools_not_return_threshold() -> None:
    _, stocks = calculated()
    stock = next(item for item in stocks if item.symbol == "600001.SH")
    assert stock.is_limit_up is False and stock.is_broken_board is True


def test_29_eod_only_is_explicit() -> None:
    sectors, stocks = calculated()
    assert all(QualityFlag.EOD_ONLY in item.meta.quality_flags for item in [*sectors, *stocks])


def test_30_market_sector_stock_links_are_complete() -> None:
    sectors, stocks = calculated()
    by_id = {item.sector_id: item for item in sectors}
    stock = next(item for item in stocks if item.symbol == "600001.SH")
    assert stock.market_snapshot_id == market().meta.snapshot_id
    assert stock.sector_snapshot_id == by_id[stock.industry_sector_id].meta.snapshot_id
    assert stock.universe_version == universe().universe_version


def test_31_batch_repository_as_of_and_record_source(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    sectors, stocks = calculated()
    repo.save_snapshots([*sectors, *stocks])
    found = repo.get_snapshot_as_of(
        snapshot_type=SnapshotType.STOCK,
        symbol_scope="600001.SH",
        cutoff=NOW,
        record_source=RecordSource.TEST,
    )
    assert found is not None and found.meta.record_source == RecordSource.TEST
    assert (
        repo.get_snapshot_as_of(
            snapshot_type=SnapshotType.STOCK,
            symbol_scope="600001.SH",
            cutoff=NOW,
            record_source=RecordSource.REAL,
        )
        is None
    )


def test_32_sector_revision_uses_shared_repository_contract(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    sector = calculated()[0][0]
    repo.save_snapshot(sector)
    corrected_meta = sector.meta.model_copy(
        update={
            "snapshot_id": sector.meta.snapshot_id + "_r2",
            "content_hash": "f" * 64,
            "finality": Finality.CORRECTED,
            "revision": 2,
            "corrects_snapshot_id": sector.meta.snapshot_id,
            "available_time": CUTOFF,
        }
    )
    corrected = sector.model_copy(update={"meta": corrected_meta})
    assert repo.save_snapshot(corrected).meta.revision == 2


class FakeSource:
    name = "FAKE"

    def fetch(self, trade_date: date) -> SectorStockRawData:
        assert trade_date == DAY
        return SectorStockRawData(
            source="FAKE",
            source_timestamp=SOURCE_TIME,
            available_time=AVAILABLE,
            securities=securities(),
            memberships=memberships(),
            bars=bars(),
            previous_bars=previous_bars(),
            source_universe_version="FAKE_V1",
            request_seconds_by_source={"fake": 0.001},
            field_provenance={"prices": "FAKE"},
            eod_only=True,
        )


def test_33_collector_batches_universe_sector_and_stock(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    result = SectorStockSentimentCollector(
        repository=repo, source=FakeSource(), clock=lambda: NOW
    ).collect(
        trade_date=DAY,
        finality=Finality.FINAL,
        record_source=RecordSource.TEST,
        market_snapshot=market(),
        persist=True,
    )
    assert len(result.sectors) == 2 and len(result.stocks) == 3
    assert result.performance.db_write_seconds >= 0


def test_34_content_hash_stable_for_same_inputs() -> None:
    first = calculated()
    second = calculated()
    assert [item.meta.content_hash for item in first[0]] == [
        item.meta.content_hash for item in second[0]
    ]
    assert [item.meta.content_hash for item in first[1]] == [
        item.meta.content_hash for item in second[1]
    ]


def test_35_intraday_rejects_eod_source(tmp_path: Path) -> None:
    collector = SectorStockSentimentCollector(
        repository=SentimentV3Repository(tmp_path / "v3.duckdb"),
        source=FakeSource(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="EOD-only"):
        collector.collect(trade_date=DAY, finality=Finality.INTRADAY, persist=False)


def test_36_no_score_or_formal_weight_added() -> None:
    sectors, stocks = calculated()
    assert not hasattr(sectors[0], "score") and not hasattr(stocks[0], "score")
    assert sectors[0].meta.formal_weight == stocks[0].meta.formal_weight == 0


def test_37_stock_report_remains_decoupled() -> None:
    import trading.research.stock_report_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "SectorStockSentimentCollector" not in source
    assert "sector_stock" not in source


def test_38_formal_and_shadow_modules_do_not_import_s2b() -> None:
    roots = [Path("trading/decision_support"), Path("trading/research/stock_report_service.py")]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    )
    assert "SectorStockSentimentCollector" not in text
    assert "StockTradingSentimentSnapshot" not in text


def test_39_batch_save_uses_one_database_connection(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    original = repo._connection
    calls = 0

    def counted():
        nonlocal calls
        calls += 1
        return original()

    repo._connection = counted
    sectors, stocks = calculated()
    repo.save_snapshots([*sectors, *stocks])
    assert calls == 1


def test_40_multiple_concepts_are_preserved_when_membership_is_supplied() -> None:
    extra = memberships(safe=True) + [
        SectorMembership(
            symbol="000001.SZ",
            sector_id="CONCEPT:A",
            sector_name="概念A",
            sector_type=SectorType.CONCEPT,
            available_time=AVAILABLE,
            source="TEST",
            membership_version="CONCEPT_V1",
            pit_safe_for_history=True,
        ),
        SectorMembership(
            symbol="000001.SZ",
            sector_id="CONCEPT:B",
            sector_name="概念B",
            sector_type=SectorType.CONCEPT,
            available_time=AVAILABLE,
            source="TEST",
            membership_version="CONCEPT_V1",
            pit_safe_for_history=True,
        ),
    ]
    sectors, stocks = calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=market(),
        memberships=extra,
        bars=bars(),
        previous_bars=previous_bars(),
    )
    stock = next(item for item in stocks if item.symbol == "000001.SZ")
    assert stock.concept_sector_ids == ["CONCEPT:A", "CONCEPT:B"]
    assert {item.sector_type for item in sectors} == {SectorType.INDUSTRY, SectorType.CONCEPT}


def test_41_identical_real_and_test_content_do_not_collide(tmp_path: Path) -> None:
    repo = SentimentV3Repository(tmp_path / "v3.duckdb")
    test_market = market()
    real_market = calculate_market_snapshot(
        replace(context(), record_source=RecordSource.REAL),
        SnapshotInputs(
            quotes=[
                MarketQuote("000001.SZ", 0.04, 100),
                MarketQuote("600001.SH", 0.07, 200),
                MarketQuote("920001.BJ", 0.02, 300),
            ],
            limit_up=[],
            limit_down_symbols=[],
            broken_board_symbols=["600001.SH"],
            strong_stock_symbols=[],
            expected_universe_size=3,
        ),
    )
    assert test_market.meta.content_hash == real_market.meta.content_hash
    assert test_market.meta.snapshot_id != real_market.meta.snapshot_id
    repo.save_snapshot(test_market)
    repo.save_snapshot(real_market)
    assert (
        repo.get_latest_snapshot(snapshot_type=SnapshotType.MARKET, record_source=RecordSource.TEST)
        is not None
    )
    assert (
        repo.get_latest_snapshot(snapshot_type=SnapshotType.MARKET, record_source=RecordSource.REAL)
        is not None
    )


def test_42_absolute_gain_can_underperform_sector() -> None:
    custom_bars = [
        replace(bars()[0], return_pct=0.04),
        replace(bars()[1], return_pct=0.10),
        replace(bars()[2], return_pct=-0.08),
    ]
    custom_memberships = [
        memberships(safe=True)[0].model_copy(
            update={"sector_id": "IND:SAME", "sector_name": "同一行业"}
        ),
        memberships(safe=True)[1].model_copy(
            update={"sector_id": "IND:SAME", "sector_name": "同一行业"}
        ),
        memberships(safe=True)[2],
    ]
    _, stocks = calculate_sector_stock_snapshots(
        context=context(),
        universe=universe(),
        market=market(),
        memberships=custom_memberships,
        bars=custom_bars,
        previous_bars=previous_bars(),
    )
    stock = next(item for item in stocks if item.symbol == "000001.SZ")
    assert stock.return_pct == pytest.approx(0.04)
    assert stock.market_return == pytest.approx(0.02)
    assert stock.sector_return == pytest.approx(0.07)
    assert stock.relative_market_return == pytest.approx(0.02)
    assert stock.relative_sector_return == pytest.approx(-0.03)
