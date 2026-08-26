from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.research.sentiment.v3.calculators import (
    MarketQuote,
    SnapshotBuildContext,
    SnapshotInputs,
    calculate_market_snapshot,
)
from trading.research.sentiment.v3.market_providers import (
    AkshareMarketBreadthProvider,
    LocalMarketBreadthProvider,
    MarketBreadthProviderChain,
    MarketBreadthRawData,
    TencentMarketBreadthProvider,
    TushareMarketBreadthProvider,
    default_market_breadth_chain,
    sanitize_error,
)
from trading.research.sentiment.v3.schemas import (
    Finality,
    ProviderHealth,
    QualityFlag,
    RecordSource,
)


TZ = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 8, 13)
CUTOFF = datetime(2026, 8, 13, 14, 30, tzinfo=TZ)


class Provider:
    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        timestamp: datetime = CUTOFF - timedelta(seconds=1),
    ) -> None:
        self.name = name
        self.fail = fail
        self.timestamp = timestamp
        self.calls = 0

    def fetch(self, *, trade_date, data_cutoff, finality):
        del trade_date, data_cutoff, finality
        self.calls += 1
        if self.fail:
            raise ConnectionError("token=secret network unavailable")
        quotes = [
            MarketQuote("000001.SZ", 0.01, 100.0),
            MarketQuote("600001.SH", -0.02, 200.0),
        ]
        return MarketBreadthRawData(
            source=self.name,
            source_timestamp=self.timestamp,
            available_time=self.timestamp,
            symbols=quotes,
            expected_universe_size=2,
            coverage_ratio=1.0,
            field_provenance={"advancers": self.name, "market_turnover": self.name},
        )


def resolve(*providers: Provider):
    return MarketBreadthProviderChain(list(providers)).resolve(
        trade_date=TRADE_DATE,
        data_cutoff=CUTOFF,
        finality=Finality.INTRADAY,
    )


def test_01_primary_success_stops_fallback() -> None:
    primary = Provider("primary")
    fallback = Provider("fallback")
    result = resolve(primary, fallback)
    assert result.data is not None and result.data.source == "primary"
    assert primary.calls == 1 and fallback.calls == 0
    assert QualityFlag.FALLBACK_USED not in result.quality_flags


def test_02_primary_failure_fallback_success() -> None:
    primary = Provider("primary", fail=True)
    fallback = Provider("fallback")
    result = resolve(primary, fallback)
    assert result.data is not None and result.data.source == "fallback"
    assert QualityFlag.PRIMARY_SOURCE_FAILED in result.quality_flags
    assert QualityFlag.FALLBACK_USED in result.quality_flags
    assert [health.success for health in result.provider_health] == [False, True]
    assert result.provider_health[1].fallback_used is True


def test_03_all_providers_fail_without_raising() -> None:
    result = resolve(Provider("a", fail=True), Provider("b", fail=True))
    assert result.data is None
    assert QualityFlag.PARTIAL_DATA in result.quality_flags
    assert QualityFlag.MARKET_BREADTH_UNAVAILABLE in result.quality_flags
    assert QualityFlag.SOURCE_UNAVAILABLE in result.quality_flags


def test_04_failed_market_fields_remain_missing_not_zero() -> None:
    result = resolve(Provider("a", fail=True))
    assert result.data is None
    assert all(health.record_count == 0 for health in result.provider_health)


def test_05_provenance_names_selected_provider() -> None:
    result = resolve(Provider("primary", fail=True), Provider("fallback"))
    assert result.data is not None
    assert result.data.field_provenance == {
        "advancers": "fallback",
        "market_turnover": "fallback",
    }


def test_06_provider_latency_is_recorded() -> None:
    result = resolve(Provider("primary", fail=True), Provider("fallback"))
    assert set(result.latency_by_provider_ms) == {"primary", "fallback"}
    assert all(health.latency_ms >= 0 for health in result.provider_health)


def test_07_health_error_is_sanitized() -> None:
    result = resolve(Provider("primary", fail=True))
    message = result.provider_health[0].error_message_sanitized or ""
    assert "secret" not in message
    assert "<redacted>" in message
    assert sanitize_error(RuntimeError("cookie=private")) == "cookie=<redacted>"


def _local_db(path: Path, snapshot_time: datetime) -> None:
    import duckdb

    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE market_snapshot_runs (
                snapshot_id VARCHAR, mode VARCHAR, provider VARCHAR,
                data_cutoff TIMESTAMPTZ, expected_universe_size BIGINT,
                valid_symbol_count BIGINT, coverage_ratio DOUBLE,
                snapshot_time TIMESTAMPTZ, completed_at TIMESTAMPTZ,
                completeness_status VARCHAR
            );
            CREATE TABLE market_snapshot_items (
                snapshot_id VARCHAR, symbol VARCHAR, change_pct DOUBLE,
                amount DOUBLE, item_status VARCHAR
            );
            """
        )
        connection.execute(
            "INSERT INTO market_snapshot_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                "s1",
                "APPLY",
                "TEST",
                snapshot_time,
                2,
                2,
                1.0,
                snapshot_time,
                snapshot_time,
                "COMPLETE",
            ],
        )
        connection.executemany(
            "INSERT INTO market_snapshot_items VALUES (?,?,?,?,?)",
            [
                ["s1", "000001.SZ", 0.01, 100.0, "VALID"],
                ["s1", "600001.SH", -0.01, 200.0, "VALID"],
            ],
        )


def test_08_fresh_local_snapshot_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "local.duckdb"
    _local_db(path, CUTOFF - timedelta(minutes=5))
    result = LocalMarketBreadthProvider(path).fetch(
        trade_date=TRADE_DATE,
        data_cutoff=CUTOFF,
        finality=Finality.INTRADAY,
    )
    assert len(result.symbols) == 2
    assert result.coverage_ratio == 1.0


def test_09_stale_local_snapshot_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local.duckdb"
    _local_db(path, CUTOFF - timedelta(minutes=11))
    with pytest.raises(RuntimeError, match="stale"):
        LocalMarketBreadthProvider(path).fetch(
            trade_date=TRADE_DATE,
            data_cutoff=CUTOFF,
            finality=Finality.INTRADAY,
        )



def test_09b_stale_local_failure_is_preserved_in_chain_quality(tmp_path: Path) -> None:
    path = tmp_path / "local-chain.duckdb"
    _local_db(path, CUTOFF - timedelta(minutes=11))
    result = MarketBreadthProviderChain(
        [LocalMarketBreadthProvider(path), Provider("fallback")]
    ).resolve(
        trade_date=TRADE_DATE,
        data_cutoff=CUTOFF,
        finality=Finality.INTRADAY,
    )
    assert result.data is not None
    assert QualityFlag.STALE_DATA in result.quality_flags
    assert QualityFlag.FALLBACK_USED in result.quality_flags

def test_10_previous_day_local_snapshot_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local.duckdb"
    _local_db(path, CUTOFF - timedelta(days=1))
    with pytest.raises(RuntimeError, match="trade_date is stale"):
        LocalMarketBreadthProvider(path).fetch(
            trade_date=TRADE_DATE,
            data_cutoff=CUTOFF,
            finality=Finality.INTRADAY,
        )


def test_11_failure_does_not_change_pit_timestamp() -> None:
    timestamp = CUTOFF - timedelta(seconds=5)
    result = resolve(Provider("a", fail=True), Provider("b", timestamp=timestamp))
    assert result.data is not None
    assert result.data.available_time == timestamp
    assert result.data.source_timestamp == timestamp


def test_12_stock_report_still_has_no_v3_provider_dependency() -> None:
    import trading.research.stock_report_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "market_providers" not in source
    assert "MarketBreadthProviderChain" not in source


def test_13_provider_chain_has_no_score_or_weight() -> None:
    result = resolve(Provider("primary"))
    assert result.data is not None
    assert not hasattr(result.data, "score")
    assert not hasattr(result.data, "formal_weight")


def test_14_error_diagnostics_do_not_include_traceback() -> None:
    result = resolve(Provider("primary", fail=True))
    health = result.provider_health[0]
    assert health.error_type == "ConnectionError"
    assert "Traceback" not in (health.error_message_sanitized or "")


def test_15_content_is_stable_for_identical_provider_data() -> None:
    first = resolve(Provider("primary"))
    second = resolve(Provider("primary"))
    assert first.data == second.data


def test_16_provider_latency_does_not_change_content_hash() -> None:
    context = SnapshotBuildContext(
        trade_date=TRADE_DATE,
        source="TEST",
        source_timestamp=CUTOFF - timedelta(seconds=3),
        available_time=CUTOFF - timedelta(seconds=2),
        data_cutoff=CUTOFF - timedelta(seconds=1),
        snapshot_time=CUTOFF,
        finality=Finality.INTRADAY,
        record_source=RecordSource.TEST,
        data_version="v1",
        created_at=CUTOFF,
        quality_flags=(QualityFlag.NOT_FINAL,),
    )
    base = dict(
        quotes=[MarketQuote("000001.SZ", 0.01, 100.0)],
        limit_up=[],
        limit_down_symbols=[],
        broken_board_symbols=[],
        strong_stock_symbols=[],
        field_provenance={"advancers": "TEST"},
    )
    def health(latency: float) -> ProviderHealth:
        return ProviderHealth(
            provider="TEST",
            success=True,
            latency_ms=latency,
            record_count=1,
            coverage_ratio=1,
            source_timestamp=CUTOFF - timedelta(seconds=3),
        )
    first = calculate_market_snapshot(
        context, SnapshotInputs(**base, provider_health=(health(1),))
    )
    second = calculate_market_snapshot(
        context, SnapshotInputs(**base, provider_health=(health(999),))
    )
    assert first.meta.content_hash == second.meta.content_hash
    assert first.provider_health != second.provider_health

def test_17_default_intraday_provider_order() -> None:
    chain = default_market_breadth_chain(finality=Finality.INTRADAY)
    assert [type(provider) for provider in chain.providers] == [
        AkshareMarketBreadthProvider,
        LocalMarketBreadthProvider,
        TencentMarketBreadthProvider,
    ]


def test_18_final_chain_excludes_local_snapshot_without_finality() -> None:
    chain = default_market_breadth_chain(finality=Finality.FINAL)
    assert [type(provider) for provider in chain.providers] == [
        TushareMarketBreadthProvider,
        AkshareMarketBreadthProvider,
        TencentMarketBreadthProvider,
    ]