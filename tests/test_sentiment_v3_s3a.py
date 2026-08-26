from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from trading.research.sentiment.v3.evaluation import (
    EvaluationConfig,
    EvaluationQualityFlag,
    ResearchEvaluationDatasetBuilder,
    ResearchEvaluator,
)
from trading.research.sentiment.v3.feature_assembly import (
    PITStatus,
    PacketQualityStatus,
    PacketType,
    ResearchFeaturePacket,
)
from trading.research.sentiment.v3.feature_registry import FEATURE_REGISTRY_VERSION
from trading.research.sentiment.v3.label_repository import (
    SentimentV3EvaluationRepository,
)
from trading.research.sentiment.v3.labels import (
    EntryRule,
    ExecutionStatus,
    ForwardLabelBuilder,
    LabelKind,
    LabelQualityFlag,
    LabelSpec,
    LabelStatus,
    MissingReason,
    PriceBar,
    PriceBatch,
    TradabilityStatus,
    label_coverage_report,
    label_specs_v1,
)
from trading.research.sentiment.v3.schemas import RecordSource


TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 7, 3)
CUTOFF = datetime(2026, 7, 3, 20, tzinfo=TZ)
BUILD_AS_OF = datetime(2026, 8, 14, 20, tzinfo=TZ)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def packet(symbol: str = "000001.SZ", *, day: date = DAY, value: float = 1.0):
    cutoff = datetime.combine(day, time(20), tzinfo=TZ)
    return ResearchFeaturePacket(
        packet_id=f"packet-{symbol}-{day}",
        packet_type=PacketType.EOD_RESEARCH_PACKET,
        symbol=symbol,
        trade_date=day,
        decision_time=cutoff,
        cutoff=cutoff,
        record_source=RecordSource.TEST,
        universe_id="u1",
        universe_version="A_SHARE_UNIVERSE_RULES_V1",
        pit_status=PITStatus.PIT_SAFE,
        quality_status=PacketQualityStatus.COMPLETE,
        features={"market": {"test_feature": value}},
        feature_names=["market.test_feature"],
        feature_quality={},
        feature_missing_reasons={},
        feature_lineage={},
        coverage={"market": 1.0},
        created_at=cutoff,
        content_hash=digest(f"packet:{symbol}:{day}:{value}"),
    )


def trading_days(start: date, count: int) -> list[date]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


class MemoryPriceSource:
    def __init__(self, calendar: list[date], bars: dict[str, dict[date, PriceBar]]):
        self.calendar = calendar
        self.bars = bars
        self.calls = 0

    def load_batch(self, **_: object) -> PriceBatch:
        self.calls += 1
        return PriceBatch(
            calendar=self.calendar,
            bars=self.bars,
            price_source="TEST_LOCAL",
            price_data_version="prices-v1",
            loaded_at=BUILD_AS_OF,
            calendar_load_ms=0.1,
            price_load_ms=0.2,
        )


def bars_for(
    symbol: str = "000001.SZ",
    *,
    start_price: float = 10.0,
    factor_change: bool = False,
) -> tuple[list[date], dict[str, dict[date, PriceBar]]]:
    calendar = trading_days(DAY, 25)
    values: dict[date, PriceBar] = {}
    previous = start_price
    for index, day in enumerate(calendar):
        factor = 2.0 if factor_change and index >= 2 else 1.0
        raw_base = start_price + index if not factor_change else (start_price + index) / factor
        values[day] = PriceBar(
            symbol=symbol,
            trade_date=day,
            open=raw_base,
            high=raw_base * 1.05,
            low=raw_base * 0.95,
            close=raw_base * 1.01,
            volume=1000,
            amount=raw_base * 1000,
            previous_close=previous,
            adjustment_factor=factor,
            source_version=f"bar-{index}",
        )
        previous = raw_base * 1.01
    return calendar, {symbol: values}


def build_one(
    spec: LabelSpec,
    *,
    source: MemoryPriceSource | None = None,
    item: ResearchFeaturePacket | None = None,
):
    if source is None:
        calendar, bars = bars_for()
        source = MemoryPriceSource(calendar, bars)
    return ForwardLabelBuilder(price_source=source).build_for_packet(
        item or packet(), [spec], build_as_of=BUILD_AS_OF
    )[0]


def executable(horizon: int = 1) -> LabelSpec:
    return LabelSpec(
        label_name=f"NEXT_OPEN_TO_CLOSE_{horizon}D",
        label_kind=LabelKind.EXECUTABLE,
        entry_rule=EntryRule.NEXT_TRADING_DAY_OPEN,
        horizon=horizon,
        execution_realistic=True,
    )


def statistical(horizon: int = 1) -> LabelSpec:
    return LabelSpec(
        label_name=f"CLOSE_TO_CLOSE_{horizon}D",
        label_kind=LabelKind.STATISTICAL,
        entry_rule=EntryRule.SIGNAL_CLOSE_REFERENCE,
        horizon=horizon,
        execution_realistic=False,
    )


def test_01_label_specs_have_statistical_and_executable_1_3_5_10_20():
    specs = label_specs_v1()
    assert len(specs) == 10
    assert {item.horizon for item in specs} == {1, 3, 5, 10, 20}
    assert {item.label_kind for item in specs} == {LabelKind.STATISTICAL, LabelKind.EXECUTABLE}


def test_02_eod_executable_entry_is_strictly_next_trading_day():
    label = build_one(executable())
    assert label.entry_trade_date == date(2026, 7, 6)
    assert label.entry_time > label.feature_cutoff
    assert label.exit_trade_date == date(2026, 7, 7)


def test_03_weekend_and_horizon_use_trading_days():
    label = build_one(executable(5))
    assert label.entry_trade_date == date(2026, 7, 6)
    assert label.exit_trade_date == date(2026, 7, 10)
    assert label.holding_trading_days == 5


def test_04_statistical_close_reference_is_not_execution_realistic():
    label = build_one(statistical())
    assert label.entry_trade_date == DAY
    assert label.entry_price_type == EntryRule.SIGNAL_CLOSE_REFERENCE
    assert not label.execution_realistic
    assert label.tradability_status == TradabilityStatus.RESEARCH_RETURN_ONLY
    assert LabelQualityFlag.RESEARCH_RETURN_ONLY in label.quality_flags


def test_05_adjustment_factor_preserves_corporate_action_continuity():
    calendar, bars = bars_for(factor_change=True)
    label = build_one(executable(3), source=MemoryPriceSource(calendar, bars))
    assert label.raw_return != pytest.approx(label.adjusted_return)
    assert label.adjusted_return is not None


def test_06_entry_and_exit_use_same_adjustment_method():
    label = build_one(executable(3))
    assert label.price_adjustment_method == "ADJ_FACTOR_RETURN_V1"
    assert label.adjusted_return is not None


def test_07_missing_adjustment_is_invalid_not_zero():
    calendar, bars = bars_for()
    bars["000001.SZ"][calendar[1]] = bars["000001.SZ"][calendar[1]].model_copy(
        update={"adjustment_factor": None}
    )
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars))
    assert label.adjusted_return is None
    assert label.missing_reason == MissingReason.ADJUSTMENT_UNAVAILABLE


def test_08_suspended_entry_is_invalid_without_rolling_forward():
    calendar, bars = bars_for()
    bars["000001.SZ"][calendar[1]] = bars["000001.SZ"][calendar[1]].model_copy(
        update={"suspended": True, "volume": 0}
    )
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars))
    assert label.entry_status == ExecutionStatus.SUSPENDED
    assert label.label_status == LabelStatus.INVALID
    assert label.entry_trade_date == calendar[1]


def test_09_one_price_limit_up_entry_is_unexecutable():
    calendar, bars = bars_for()
    previous = bars["000001.SZ"][calendar[0]].close
    locked = float(previous) * 1.1
    bars["000001.SZ"][calendar[1]] = bars["000001.SZ"][calendar[1]].model_copy(
        update={"open": locked, "high": locked, "low": locked, "close": locked,
                "previous_close": previous}
    )
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars))
    assert label.entry_status == ExecutionStatus.LIMIT_UP_LOCKED
    assert label.adjusted_return is None


def test_10_limit_up_opened_is_distinguished_and_inferred():
    calendar, bars = bars_for()
    previous = bars["000001.SZ"][calendar[0]].close
    limit_price = float(previous) * 1.1
    bars["000001.SZ"][calendar[1]] = bars["000001.SZ"][calendar[1]].model_copy(
        update={"open": limit_price, "high": limit_price, "low": limit_price * 0.98,
                "close": limit_price * 0.99, "previous_close": previous}
    )
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars))
    assert label.entry_status == ExecutionStatus.EXECUTABLE
    assert LabelQualityFlag.TRADABILITY_INFERRED in label.quality_flags


def test_11_locked_limit_down_exit_is_invalid():
    calendar, bars = bars_for()
    previous = bars["000001.SZ"][calendar[0]].close
    locked = float(previous) * 0.9
    bars["000001.SZ"][calendar[2]] = bars["000001.SZ"][calendar[2]].model_copy(
        update={"open": locked, "high": locked, "low": locked, "close": locked,
                "previous_close": previous}
    )
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars))
    assert label.exit_status == ExecutionStatus.LIMIT_DOWN_LOCKED
    assert label.missing_reason == MissingReason.LIMIT_DOWN_LOCKED_EXIT


def test_12_exit_suspension_and_missing_quote_are_explicit():
    calendar, bars = bars_for()
    bars["000001.SZ"][calendar[3]] = bars["000001.SZ"][calendar[3]].model_copy(
        update={"suspended": True, "volume": 0, "close": None}
    )
    label = build_one(executable(3), source=MemoryPriceSource(calendar, bars))
    assert label.exit_status == ExecutionStatus.SUSPENDED
    assert label.missing_reason == MissingReason.SUSPENDED_EXIT

def test_13_delisting_missing_exit_is_not_silently_dropped():
    calendar, bars = bars_for()
    bars["000001.SZ"][calendar[3]] = bars["000001.SZ"][calendar[3]].model_copy(
        update={"delisted": True, "close": None}
    )
    label = build_one(executable(3), source=MemoryPriceSource(calendar, bars))
    report = label_coverage_report([label])
    assert label.missing_reason == MissingReason.DELISTED
    assert report.invalid_labels == 1
    assert report.survivorship_audit[LabelQualityFlag.DELISTING_RELATED.value] == 1


def test_14_insufficient_forward_window_is_explicit():
    calendar, bars = bars_for()
    short = MemoryPriceSource(calendar[:3], {"000001.SZ": dict(list(bars["000001.SZ"].items())[:3])})
    label = build_one(executable(5), source=short)
    assert label.label_status == LabelStatus.INSUFFICIENT_FORWARD_WINDOW
    assert label.adjusted_return is None


def test_15_mae_mfe_use_adjusted_holding_window_prices():
    label = build_one(executable(3))
    assert label.max_adverse_excursion is not None
    assert label.max_favorable_excursion is not None
    assert label.max_adverse_excursion < label.max_favorable_excursion


def test_16_no_cost_or_slippage_is_invented():
    label = build_one(executable())
    assert label.net_return is None
    assert label.cost_model_version is None
    assert LabelQualityFlag.COST_MODEL_NOT_CONFIGURED in label.quality_flags


def test_17_label_id_and_hash_are_deterministic():
    first = build_one(executable(5))
    second = build_one(executable(5))
    assert first.label_id == second.label_id
    assert first.content_hash == second.content_hash


def test_18_batch_builder_loads_prices_once_not_n_plus_one():
    calendar = trading_days(DAY, 25)
    packets = [packet(f"{index:06d}.SZ", value=index) for index in range(1, 101)]
    all_bars = {}
    for item in packets:
        _, value = bars_for(item.symbol)
        all_bars.update(value)
    source = MemoryPriceSource(calendar, all_bars)
    result = ForwardLabelBuilder(price_source=source).build_batch(
        packets, [executable(1), executable(5)], build_as_of=BUILD_AS_OF
    )
    assert len(result.labels) == 200
    assert source.calls == 1
    assert result.metrics.database_query_count == 3


def test_19_label_repository_batch_save_read_and_dedup(tmp_path):
    repo = SentimentV3EvaluationRepository(tmp_path / "labels.duckdb")
    label = build_one(executable())
    repo.save_forward_labels([label, label])
    assert repo.get_forward_label(label.label_id) == label
    assert len(repo.list_forward_labels(record_source=RecordSource.TEST)) == 1


def test_20_dataset_join_uses_packet_id_symbol_date_cutoff_and_is_deterministic():
    item = packet()
    label = build_one(executable(), item=item)
    kwargs = dict(
        packets=[item], labels=[label], feature_registry_version=FEATURE_REGISTRY_VERSION,
        created_at=BUILD_AS_OF,
    )
    first = ResearchEvaluationDatasetBuilder.build(**kwargs)
    second = ResearchEvaluationDatasetBuilder.build(**kwargs)
    assert first.definition.dataset_id == second.definition.dataset_id
    assert first.definition.content_hash == second.definition.content_hash
    assert first.definition.original_row_count == 1


def test_21_invalid_labels_remain_in_dataset_with_exclusion_reason():
    calendar, bars = bars_for()
    del bars["000001.SZ"][calendar[1]]
    item = packet()
    label = build_one(executable(), source=MemoryPriceSource(calendar, bars), item=item)
    dataset = ResearchEvaluationDatasetBuilder.build(
        packets=[item], labels=[label], feature_registry_version=FEATURE_REGISTRY_VERSION,
        created_at=BUILD_AS_OF,
    )
    assert len(dataset.rows) == 1
    assert dataset.definition.valid_label_count == 0
    assert dataset.definition.excluded_label_count == 1


def test_22_record_sources_cannot_be_mixed():
    first = packet()
    second = packet("000002.SZ").model_copy(update={"record_source": RecordSource.REPLAY})
    labels = [build_one(executable(), item=first)]
    calendar, value = bars_for("000002.SZ")
    labels.append(build_one(executable(), source=MemoryPriceSource(calendar, value), item=second))
    with pytest.raises(ValueError, match="cannot be mixed"):
        ResearchEvaluationDatasetBuilder.build(
            packets=[first, second], labels=labels,
            feature_registry_version=FEATURE_REGISTRY_VERSION, created_at=BUILD_AS_OF,
        )


def evaluation_dataset(days: int = 2, per_day: int = 20):
    packets = []
    labels = []
    spec = executable(5)
    calendar = trading_days(DAY, 30)
    for day_index in range(days):
        signal_day = calendar[day_index]
        for index in range(per_day):
            symbol = f"{day_index * per_day + index + 1:06d}.SZ"
            item = packet(symbol, day=signal_day, value=float(index))
            packets.append(item)
            _, values = bars_for(symbol, start_price=10 + index)
            label = build_one(spec, source=MemoryPriceSource(calendar, values), item=item)
            labels.append(
                label.model_copy(
                    update={
                        "adjusted_return": index / 100,
                        "raw_return": index / 100,
                        "max_adverse_excursion": -index / 200,
                        "max_favorable_excursion": index / 150,
                    }
                )
            )
    return (
        ResearchEvaluationDatasetBuilder.build(
            packets=packets, labels=labels,
            feature_registry_version=FEATURE_REGISTRY_VERSION, created_at=BUILD_AS_OF,
        ),
        spec,
    )


def test_23_pearson_and_spearman_are_cross_sectional_by_date():
    dataset, spec = evaluation_dataset()
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert len(result.daily_correlations) == 2
    assert result.ic_mean == pytest.approx(1.0)
    assert result.rank_ic_mean == pytest.approx(1.0)
    assert result.valid_dates == 2


def test_24_all_dates_pooled_ic_is_forbidden_by_config():
    assert EvaluationConfig().pooled_all_dates_ic_allowed is False


def test_25_small_cross_section_does_not_force_ic():
    dataset, spec = evaluation_dataset(days=1, per_day=5)
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert result.ic_mean is None
    assert EvaluationQualityFlag.SAMPLE_TOO_SMALL in result.daily_correlations[0].quality_flags


def test_26_quantile_5_10_and_top_bottom_10_20_are_supported():
    dataset, spec = evaluation_dataset(per_day=25)
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert set(result.quantile_stats) == {5, 10}
    assert set(result.top_n_stats) == {10, 20}
    assert result.top_n_stats[10].top.mean_return > result.top_n_stats[10].bottom.mean_return


def test_27_mean_median_mae_mfe_and_tail_exclusion_are_preserved():
    dataset, spec = evaluation_dataset()
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert result.overall_return_stats.mean_return is not None
    assert result.overall_return_stats.median_return is not None
    assert result.overall_return_stats.mae_mean is not None
    assert result.overall_return_stats.mfe_mean is not None
    assert set(result.tail_exclusion_stats) == {1, 5, 10}


def test_28_profit_factor_definition_and_zero_denominator():
    dataset, spec = evaluation_dataset(days=1)
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert result.overall_return_stats.profit_factor is None
    assert EvaluationQualityFlag.PROFIT_FACTOR_DENOMINATOR_ZERO in (
        result.overall_return_stats.quality_flags
    )


def test_29_cross_sectional_framework_does_not_compute_max_drawdown():
    assert EvaluationConfig().portfolio_max_drawdown_enabled is False


def test_30_overlapping_label_is_explicit():
    label = build_one(executable(5))
    assert label.overlapping_label is True
    dataset = ResearchEvaluationDatasetBuilder.build(
        packets=[packet()], labels=[label],
        feature_registry_version=FEATURE_REGISTRY_VERSION, created_at=BUILD_AS_OF,
    )
    result = ResearchEvaluator(EvaluationConfig(min_cross_section_size=3)).evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=executable(5)
    )
    assert result.overlapping_labels is True


def test_31_framework_returns_no_alpha_conclusion_or_weight():
    dataset, spec = evaluation_dataset()
    result = ResearchEvaluator().evaluate_feature(
        dataset, feature_name="market.test_feature", label_spec=spec
    )
    assert result.alpha_conclusion == "NONE"
    assert result.formal_weight == 0


def test_32_feature_registry_v1_count_and_history_safe_count_remain_frozen():
    from trading.research.sentiment.v3.feature_registry import FEATURE_REGISTRY_V1

    assert len(FEATURE_REGISTRY_V1.canonical_definitions()) == 124
    assert len(FEATURE_REGISTRY_V1.history_safe()) == 39


def test_33_no_label_component_exposes_trade_actions():
    forbidden = {"buy", "sell", "hold", "veto", "position", "order"}
    assert not forbidden & set(type(build_one(executable())).model_fields)


def test_34_batch_performance_for_1000_labels_is_in_memory_and_bounded():
    calendar = trading_days(DAY, 25)
    packets = [packet(f"{index:06d}.SZ", value=index) for index in range(1, 201)]
    all_bars = {}
    for item in packets:
        _, value = bars_for(item.symbol)
        all_bars.update(value)
    started = perf_counter()
    result = ForwardLabelBuilder(
        price_source=MemoryPriceSource(calendar, all_bars)
    ).build_batch(packets, [executable(i) for i in (1, 3, 5, 10, 20)], build_as_of=BUILD_AS_OF)
    elapsed = perf_counter() - started
    assert len(result.labels) == 1000
    assert elapsed < 5

