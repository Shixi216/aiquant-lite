from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading.research.sentiment.v3.calculators import (
    BROKEN_RATE_DEFINITION,
    LimitUpStock,
    MarketQuote,
    SnapshotBuildContext,
    SnapshotInputs,
    calculate_market_snapshot,
    calculate_speculation_snapshot,
)
from trading.research.sentiment.v3.collectors import (
    FetchedFrame,
    SentimentV3Collector,
)
from trading.research.sentiment.v3.hashing import stable_content_hash
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    Finality,
    MarketSentimentSnapshot,
    QualityFlag,
    RecordSource,
    SnapshotType,
    SpeculationSentimentSnapshot,
)


TZ = ZoneInfo("Asia/Shanghai")
DAY1 = date(2026, 8, 12)
DAY2 = date(2026, 8, 13)
DAY1_FINAL = datetime(2026, 8, 12, 15, 30, tzinfo=TZ)
DAY2_INTRADAY = datetime(2026, 8, 13, 14, 30, tzinfo=TZ)
DAY2_FINAL = datetime(2026, 8, 13, 15, 30, tzinfo=TZ)


@pytest.fixture
def repository(tmp_path: Path) -> SentimentV3Repository:
    return SentimentV3Repository(tmp_path / "sentiment_v3.duckdb")


def context(
    *,
    trade_date: date = DAY2,
    timestamp: datetime = DAY2_FINAL,
    finality: Finality = Finality.FINAL,
    record_source: RecordSource = RecordSource.TEST,
    revision: int = 1,
    corrects: str | None = None,
) -> SnapshotBuildContext:
    flags = (
        (QualityFlag.NOT_FINAL,)
        if finality in {Finality.INTRADAY, Finality.PRELIMINARY}
        else ()
    )
    return SnapshotBuildContext(
        trade_date=trade_date,
        source="TEST_SOURCE",
        source_timestamp=timestamp - timedelta(minutes=2),
        available_time=timestamp - timedelta(minutes=1),
        data_cutoff=timestamp - timedelta(seconds=30),
        snapshot_time=timestamp,
        finality=finality,
        record_source=record_source,
        data_version="test-v1",
        created_at=timestamp,
        revision=revision,
        corrects_snapshot_id=corrects,
        quality_flags=flags,
    )


def inputs(
    *,
    previous_market: MarketSentimentSnapshot | None = None,
    previous_speculation: SpeculationSentimentSnapshot | None = None,
) -> SnapshotInputs:
    return SnapshotInputs(
        quotes=[
            MarketQuote("000001.SZ", 0.01, 100.0),
            MarketQuote("000002.SZ", -0.02, 200.0),
            MarketQuote("600001.SH", 0.0, 300.0),
            MarketQuote("600002.SH", 0.03, 400.0),
        ],
        limit_up=[
            LimitUpStock("000001.SZ", 1),
            LimitUpStock("000002.SZ", 2),
            LimitUpStock("600001.SH", 3),
            LimitUpStock("600002.SH", 5),
        ],
        limit_down_symbols=["300001.SZ"],
        broken_board_symbols=["300002.SZ", "300003.SZ"],
        strong_stock_symbols=["000001.SZ", "600002.SH"],
        expected_universe_size=4,
        previous_market=previous_market,
        previous_speculation=previous_speculation,
    )


def snapshot_pair(
    *,
    ctx: SnapshotBuildContext | None = None,
    data: SnapshotInputs | None = None,
) -> tuple[MarketSentimentSnapshot, SpeculationSentimentSnapshot]:
    actual_context = ctx or context()
    actual_inputs = data or inputs()
    return (
        calculate_market_snapshot(actual_context, actual_inputs),
        calculate_speculation_snapshot(actual_context, actual_inputs),
    )


def test_01_snapshot_time_fields_are_distinct_and_ordered() -> None:
    market, _ = snapshot_pair()
    meta = market.meta
    assert meta.source_timestamp < meta.available_time < meta.data_cutoff
    assert meta.data_cutoff < meta.snapshot_time == meta.created_at


def test_02_naive_snapshot_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        snapshot_pair(ctx=context(timestamp=datetime(2026, 8, 13, 15, 30)))


def test_03_invalid_pit_time_order_is_rejected() -> None:
    bad = replace(context(), available_time=DAY2_FINAL + timedelta(minutes=1))
    with pytest.raises(ValueError, match="PIT order"):
        snapshot_pair(ctx=bad)


def test_04_non_final_snapshots_are_explicitly_flagged() -> None:
    market, speculation = snapshot_pair(
        ctx=context(timestamp=DAY2_INTRADAY, finality=Finality.INTRADAY)
    )
    assert QualityFlag.NOT_FINAL in market.meta.quality_flags
    assert QualityFlag.NOT_FINAL in speculation.meta.quality_flags


def test_05_content_hash_is_stable_across_collection_times() -> None:
    first, _ = snapshot_pair(ctx=context(timestamp=DAY2_FINAL))
    later_context = context(timestamp=DAY2_FINAL + timedelta(minutes=5))
    later, _ = snapshot_pair(ctx=later_context)
    assert first.meta.content_hash == later.meta.content_hash
    assert first.meta.snapshot_id == later.meta.snapshot_id


def test_06_hash_changes_with_algorithm_version() -> None:
    payload = {"advancers": 1}
    base = dict(
        snapshot_type=SnapshotType.MARKET,
        source="TEST",
        trade_date=DAY2,
        symbol_scope="ALL_A",
        schema_version="1",
        normalized_payload=payload,
    )
    assert stable_content_hash(**base, algorithm_version="a") != stable_content_hash(
        **base, algorithm_version="b"
    )


def test_07_repository_deduplicates_identical_content(
    repository: SentimentV3Repository,
) -> None:
    first, _ = snapshot_pair()
    second, _ = snapshot_pair(ctx=context(timestamp=DAY2_FINAL + timedelta(minutes=5)))
    assert repository.save_snapshot(first).meta.snapshot_id == repository.save_snapshot(
        second
    ).meta.snapshot_id
    assert len(
        repository.list_snapshots(
            snapshot_type=SnapshotType.MARKET,
            record_source=RecordSource.TEST,
        )
    ) == 1


def test_08_as_of_query_uses_available_time_not_created_at(
    repository: SentimentV3Repository,
) -> None:
    older, _ = snapshot_pair(ctx=context(timestamp=DAY1_FINAL, trade_date=DAY1))
    newer, _ = snapshot_pair(ctx=context(timestamp=DAY2_FINAL, trade_date=DAY2))
    repository.save_snapshot(older)
    repository.save_snapshot(newer)
    cutoff = DAY2_FINAL - timedelta(hours=1)
    found = repository.get_snapshot_as_of(
        snapshot_type=SnapshotType.MARKET,
        cutoff=cutoff,
        record_source=RecordSource.TEST,
    )
    assert found is not None
    assert found.meta.trade_date == DAY1


def test_09_record_sources_are_isolated_by_default(
    repository: SentimentV3Repository,
) -> None:
    test_snapshot, _ = snapshot_pair()
    repository.save_snapshot(test_snapshot)
    assert repository.get_latest_snapshot(snapshot_type=SnapshotType.MARKET) is None
    assert repository.get_latest_snapshot(
        snapshot_type=SnapshotType.MARKET,
        record_source=RecordSource.TEST,
    ) is not None


def test_10_correction_keeps_revision_chain(
    repository: SentimentV3Repository,
) -> None:
    original, _ = snapshot_pair()
    original = repository.save_snapshot(original)
    corrected_inputs = replace(
        inputs(),
        strong_stock_symbols=["000001.SZ", "600002.SH", "600003.SH"],
    )
    corrected, _ = snapshot_pair(
        ctx=context(
            timestamp=DAY2_FINAL + timedelta(minutes=10),
            finality=Finality.CORRECTED,
            revision=2,
            corrects=original.meta.snapshot_id,
        ),
        data=corrected_inputs,
    )
    saved = repository.save_snapshot(corrected)
    assert saved.meta.revision == 2
    assert saved.meta.corrects_snapshot_id == original.meta.snapshot_id
    historical = repository.get_snapshot_as_of(
        snapshot_type=SnapshotType.MARKET,
        cutoff=DAY2_FINAL,
        record_source=RecordSource.TEST,
    )
    assert historical is not None
    assert historical.meta.snapshot_id == original.meta.snapshot_id


def test_11_missing_pool_is_none_not_zero() -> None:
    missing = replace(inputs(), broken_board_symbols=None)
    market, speculation = snapshot_pair(data=missing)
    assert market.broken_board_count is None
    assert market.broken_board_rate is None
    assert speculation.broken_board_count is None
    assert QualityFlag.MISSING_BROKEN_BOARD_POOL in market.meta.quality_flags
    assert "broken_board_rate" in speculation.meta.missing_fields


def test_12_market_breadth_preserves_numerators_and_denominator() -> None:
    market, _ = snapshot_pair()
    assert (market.advancers, market.decliners, market.unchanged) == (2, 1, 1)
    assert market.breadth_denominator == 4
    assert market.breadth_strength == pytest.approx(0.25)
    assert market.advance_decline_ratio == pytest.approx(2.0)


def test_13_limit_pools_are_counted_without_return_thresholds() -> None:
    market, _ = snapshot_pair()
    assert market.limit_up_count == 4
    assert market.limit_down_count == 1
    assert market.limit_up_down_ratio == pytest.approx(4.0)


def test_14_broken_board_rate_preserves_definition_and_denominator() -> None:
    market, speculation = snapshot_pair()
    for snapshot in (market, speculation):
        assert snapshot.broken_board_count == 2
        assert snapshot.sealed_limit_up_count == 4
        assert snapshot.broken_board_denominator == 6
        assert snapshot.broken_board_denominator_definition == BROKEN_RATE_DEFINITION
        assert snapshot.broken_board_rate == pytest.approx(2 / 6)


def test_15_board_ladder_and_max_height() -> None:
    _, speculation = snapshot_pair()
    assert speculation.first_board_count == 1
    assert speculation.second_board_count == 1
    assert speculation.third_board_count == 1
    assert speculation.high_board_count == 1
    assert speculation.max_board_height == 5
    assert speculation.board_ladder_distribution == {"1": 1, "2": 1, "3": 1, "5": 1}


def test_16_promotion_uses_frozen_previous_day_pool() -> None:
    _, previous = snapshot_pair(
        ctx=context(trade_date=DAY1, timestamp=DAY1_FINAL),
    )
    current_data = SnapshotInputs(
        quotes=inputs().quotes,
        limit_up=[
            LimitUpStock("000001.SZ", 2),
            LimitUpStock("000002.SZ", 3),
            LimitUpStock("600009.SH", 1),
        ],
        limit_down_symbols=[],
        broken_board_symbols=[],
        strong_stock_symbols=[],
        previous_speculation=previous,
    )
    _, current = snapshot_pair(data=current_data)
    assert current.promotion_denominator == 4
    assert current.promotion_count == 2
    assert current.promotion_rate == pytest.approx(0.5)
    assert current.previous_snapshot_id == previous.meta.snapshot_id


def test_17_yesterday_returns_use_previous_symbols_only() -> None:
    _, previous = snapshot_pair(
        ctx=context(trade_date=DAY1, timestamp=DAY1_FINAL),
    )
    current_data = SnapshotInputs(
        quotes=[
            MarketQuote("000001.SZ", 0.10, 1.0),
            MarketQuote("000002.SZ", -0.10, 1.0),
            MarketQuote("600001.SH", 0.02, 1.0),
            MarketQuote("600002.SH", 0.04, 1.0),
            MarketQuote("999999.SZ", 9.99, 1.0),
        ],
        limit_up=[],
        limit_down_symbols=[],
        broken_board_symbols=[],
        strong_stock_symbols=[],
        previous_speculation=previous,
    )
    _, current = snapshot_pair(data=current_data)
    assert current.yesterday_limit_up_return_count == 4
    assert current.yesterday_limit_up_avg_return == pytest.approx(0.015)
    assert current.yesterday_limit_up_median_return == pytest.approx(0.03)


def test_18_previous_snapshot_lookup_requires_finality(
    repository: SentimentV3Repository,
) -> None:
    _, intraday = snapshot_pair(
        ctx=context(
            trade_date=DAY1,
            timestamp=datetime(2026, 8, 12, 14, 30, tzinfo=TZ),
            finality=Finality.INTRADAY,
        )
    )
    repository.save_snapshot(intraday)
    assert repository.previous_final_snapshot(
        snapshot_type=SnapshotType.SPECULATION,
        before_trade_date=DAY2,
        cutoff=DAY2_FINAL,
        record_source=RecordSource.TEST,
    ) is None


class FakeSource:
    name = "FAKE_BATCH"

    def __init__(self, *, fail: str | None = None, eod_only: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.eod_only = eod_only

    def _result(self, key: str, frame: pd.DataFrame) -> FetchedFrame:
        self.calls.append(key)
        if self.fail == key:
            raise RuntimeError(f"{key} unavailable")
        return FetchedFrame(
            frame=frame,
            source_timestamp=DAY2_INTRADAY - timedelta(seconds=2),
            available_time=DAY2_INTRADAY - timedelta(seconds=1),
            request_seconds=0.001,
            eod_only=self.eod_only,
        )

    def fetch_market_quotes(self, trade_date: date) -> FetchedFrame:
        return self._result(
            "quotes",
            pd.DataFrame(
                {"代码": ["000001", "600001"], "涨跌幅": [1.0, -1.0], "成交额": [10, 20]}
            ),
        )

    def fetch_limit_up_pool(self, trade_date: date) -> FetchedFrame:
        return self._result(
            "limit_up", pd.DataFrame({"代码": ["000001"], "连板数": [2]})
        )

    def fetch_limit_down_pool(self, trade_date: date) -> FetchedFrame:
        return self._result("limit_down", pd.DataFrame({"代码": ["600001"]}))

    def fetch_broken_board_pool(self, trade_date: date) -> FetchedFrame:
        return self._result("broken", pd.DataFrame({"代码": ["300001"]}))

    def fetch_strong_stock_pool(self, trade_date: date) -> FetchedFrame:
        return self._result("strong", pd.DataFrame({"代码": ["000001"]}))


def test_19_collector_fetches_each_batch_source_once(
    repository: SentimentV3Repository,
) -> None:
    source = FakeSource()
    result = SentimentV3Collector(
        repository=repository,
        source=source,
        clock=lambda: DAY2_INTRADAY,
    ).collect(
        trade_date=DAY2,
        finality=Finality.INTRADAY,
        record_source=RecordSource.TEST,
    )
    assert sorted(source.calls) == ["broken", "limit_down", "limit_up", "quotes", "strong"]
    assert result.performance.request_count == 5
    assert result.market.limit_up_count == 1


def test_20_collector_isolates_source_failure_and_preserves_missing(
    repository: SentimentV3Repository,
) -> None:
    source = FakeSource(fail="broken")
    result = SentimentV3Collector(
        repository=repository,
        source=source,
        clock=lambda: DAY2_INTRADAY,
    ).collect(
        trade_date=DAY2,
        finality=Finality.INTRADAY,
        record_source=RecordSource.TEST,
    )
    assert result.market.broken_board_count is None
    assert QualityFlag.SOURCE_UNAVAILABLE in result.market.meta.quality_flags
    assert "broken" in result.performance.failed_sources


def test_21_eod_only_source_cannot_be_labelled_intraday(
    repository: SentimentV3Repository,
) -> None:
    with pytest.raises(ValueError, match="EOD-only"):
        SentimentV3Collector(
            repository=repository,
            source=FakeSource(eod_only=True),
            clock=lambda: DAY2_INTRADAY,
        ).collect(
            trade_date=DAY2,
            finality=Finality.INTRADAY,
            record_source=RecordSource.TEST,
        )


def test_22_final_requires_explicit_confirmation(
    repository: SentimentV3Repository,
) -> None:
    with pytest.raises(ValueError, match="confirmed_final"):
        SentimentV3Collector(
            repository=repository,
            source=FakeSource(),
            clock=lambda: DAY2_FINAL,
        ).collect(
            trade_date=DAY2,
            finality=Finality.FINAL,
            record_source=RecordSource.TEST,
        )


def test_23_stock_report_module_has_no_v3_dependency() -> None:
    import ast

    import trading.research.stock_report_service as stock_report

    source = Path(stock_report.__file__).read_text(encoding="utf-8")
    # Display labels can mention SentimentV3 without importing its implementation.
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.extend(f"{node.module}.{alias.name}" for alias in node.names)
    assert not any("sentiment.v3" in name or "SentimentV3" in name for name in imports)


def test_24_v3_snapshots_expose_no_score_action_or_decision_fields() -> None:
    market, speculation = snapshot_pair()
    forbidden = {
        "score",
        "market_score",
        "speculation_score",
        "overall_sentiment_score",
        "formal_score",
        "action",
        "confidence",
        "veto",
        "target_position",
    }
    for snapshot in (market, speculation):
        assert forbidden.isdisjoint(type(snapshot).model_fields)
        assert snapshot.meta.formal_weight == 0
        assert snapshot.meta.research_only is True


def test_25_repository_read_latency_is_local(
    repository: SentimentV3Repository,
) -> None:
    market, _ = snapshot_pair()
    repository.save_snapshot(market)
    found = repository.get_snapshot_as_of(
        snapshot_type=SnapshotType.MARKET,
        cutoff=DAY2_FINAL,
        record_source=RecordSource.TEST,
    )
    assert found is not None
