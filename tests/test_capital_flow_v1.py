from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from config.settings import settings
from data_hub.repositories import FactorOutputRepository
from database.db import get_connection, initialize_database
from database.migrations.v0105_capital_flow_v1 import apply_migration
from scripts.backfill_capital_flow import build_parser, run
from scripts.check_no_live_execution import (
    broker_import_issues,
    decision_to_real_order_issues,
    indirect_execution_issues,
)
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.research.capital_flow.aggregator import aggregate_symbol
from trading.research.capital_flow.amount_features import calculate_amount_features
from trading.research.capital_flow.financing_features import calculate_financing_features
from trading.research.capital_flow.market_features import aggregate_market
from trading.research.capital_flow.models import EstimatedFlow, FinancingRecord, MarketBar
from trading.research.capital_flow.policy import policy_for
from trading.research.capital_flow.price_volume import classify_price_volume
from trading.research.capital_flow.repository import CapitalFlowRepository
from trading.research.capital_flow.schemas import (
    CapitalFlowAnalyzeRequest,
    CapitalFlowRiskFlag,
    CapitalFlowScope,
    FinancingTrend,
    LiquidityLevel,
    PriceVolumeState,
    SubScore,
)
from trading.research.capital_flow.scorer import (
    combine_sub_scores,
    liquidity_score,
    ratio_score,
)
from trading.research.capital_flow.sector_features import aggregate_sector
from trading.research.capital_flow.service import CapitalFlowAnalysisService
from trading.research.capital_flow.turnover_features import (
    calculate_turnover_features,
    trusted_turnover,
)
from trading.research.capital_flow.units import (
    AmountUnit,
    RateUnit,
    VolumeUnit,
    normalize_amount,
    normalize_rate,
    normalize_volume,
    resolve_unit_values,
)
from trading.research.capital_flow.volume_features import calculate_volume_features
from trading.schemas import AnalysisMode, Signal


TZ = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 1, 1, 15, tzinfo=TZ)
CUTOFF = datetime(2026, 4, 1, 16, tzinfo=TZ)


def _bars(
    count: int = 61,
    *,
    symbol: str = "600000.SH",
    volume_start: float = 100,
    amount_start: float = 1_000_000,
    turnover: float | None = None,
    float_shares: float | None = None,
    sector: str | None = None,
) -> list[MarketBar]:
    return [
        MarketBar(
            canonical_record_id=f"cmr_{symbol}_{index}",
            symbol=symbol,
            event_time=START + timedelta(days=index),
            data_cutoff=START + timedelta(days=index, hours=1),
            open=10 + index * 0.01,
            high=10.1 + index * 0.01,
            low=9.9 + index * 0.01,
            close=10 + index * 0.01,
            volume=volume_start + index,
            amount=amount_start + index * 1_000,
            turnover_rate=turnover,
            float_shares=float_shares,
            sector=sector,
        )
        for index in range(count)
    ]


def _snapshot(
    bars: list[MarketBar] | None = None,
    *,
    mode: AnalysisMode = AnalysisMode.RESEARCH,
    estimated_flow: EstimatedFlow | None = None,
):
    values = bars or _bars()
    return aggregate_symbol(
        symbol=values[0].symbol,
        bars=values,
        analysis_mode=mode,
        data_cutoff=CUTOFF,
        market_amounts={values[0].symbol: values[-1].amount or 0},
        universe_complete=False,
        estimated_flow=estimated_flow,
    )


@pytest.fixture
def capital_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "capital.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def _seed_canonical(symbol: str = "600000.SH", count: int = 25) -> None:
    with get_connection() as connection:
        for index in range(count):
            event_time = START + timedelta(days=index)
            record_id = f"cmr_seed_{symbol}_{index}"
            payload = {
                "trade_date": event_time.strftime("%Y%m%d"),
                "open": 10 + index * 0.01,
                "high": 10.2 + index * 0.01,
                "low": 9.8 + index * 0.01,
                "close": 10 + index * 0.01,
                "volume": 100_000 + index * 100,
                "amount": 10_000_000 + index * 10_000,
            }
            connection.execute(
                """
                INSERT INTO canonical_market_records (
                    canonical_record_id, symbol, data_type, event_time,
                    data_cutoff, generated_at, primary_source,
                    source_record_ids_json, verification_source_ids_json,
                    verification_status, field_differences_json, payload_json,
                    confidence, content_hash, algorithm_version
                )
                VALUES (?, ?, 'daily_bar', ?, ?, ?, 'test', '[]', '[]',
                        'SINGLE_SOURCE', '{}', ?, 0.6, ?, 'test-v1')
                """,
                [
                    record_id,
                    symbol,
                    event_time,
                    event_time + timedelta(hours=1),
                    event_time + timedelta(hours=2),
                    json.dumps(payload),
                    f"{index:064x}",
                ],
            )


# A. Units and data
def test_tushare_lots_convert_to_shares() -> None:
    assert normalize_volume(123, VolumeUnit.LOTS_100).value == 12_300


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (AmountUnit.CNY, 2),
        (AmountUnit.THOUSAND_CNY, 2_000),
        (AmountUnit.TEN_THOUSAND_CNY, 20_000),
        (AmountUnit.HUNDRED_MILLION_CNY, 200_000_000),
    ],
)
def test_amount_unit_conversion(unit: AmountUnit, expected: float) -> None:
    assert normalize_amount(2, unit).value == expected


def test_unknown_unit_is_flagged() -> None:
    value = normalize_volume(10, VolumeUnit.UNKNOWN)
    assert value.value is None
    assert CapitalFlowRiskFlag.UNIT_UNKNOWN in value.flags


def test_unit_conflict_is_flagged() -> None:
    value = resolve_unit_values(
        [
            normalize_rate(10, RateUnit.PERCENT),
            normalize_rate(0.2, RateUnit.DECIMAL),
        ]
    )
    assert value.value is None
    assert CapitalFlowRiskFlag.UNIT_CONFLICT in value.flags


def test_raw_payload_is_not_mutated_by_conversion() -> None:
    payload = {"volume": 5}
    normalize_volume(payload["volume"], VolumeUnit.LOTS_100)
    assert payload == {"volume": 5}


def test_missing_value_is_not_zero() -> None:
    assert normalize_amount(None, AmountUnit.CNY).value is None


# B. Volume and amount
def test_5d_and_20d_averages_are_correct() -> None:
    bars = _bars(21, volume_start=100, amount_start=1_000)
    features = calculate_amount_features(bars)
    assert features.average_amount_5d == pytest.approx(
        sum(bar.amount or 0 for bar in bars[-6:-1]) / 5
    )
    assert features.average_amount_20d == pytest.approx(
        sum(bar.amount or 0 for bar in bars[:-1]) / 20
    )


def test_current_day_is_excluded_from_history_average() -> None:
    bars = _bars(21)
    bars[-1] = MarketBar(**{**bars[-1].__dict__, "volume": 1_000_000})
    features = calculate_volume_features(bars)
    assert features.volume_ratio_20d == pytest.approx(
        1_000_000 / (sum(bar.volume or 0 for bar in bars[:-1]) / 20)
    )


def test_insufficient_20d_history_is_flagged() -> None:
    features = calculate_volume_features(_bars(10))
    assert features.volume_ratio_20d is None
    assert CapitalFlowRiskFlag.INSUFFICIENT_HISTORY in features.risk_flags


def test_volume_ratio_20d_is_correct() -> None:
    bars = _bars(21, volume_start=100)
    assert calculate_volume_features(bars).volume_ratio_20d == pytest.approx(
        bars[-1].volume / 109.5
    )


def test_amount_ratio_20d_is_correct() -> None:
    bars = _bars(21, amount_start=1_000)
    assert calculate_amount_features(bars).amount_ratio_20d == pytest.approx(
        bars[-1].amount / 10_500
    )


def test_consecutive_volume_expansion_is_correct() -> None:
    result = calculate_volume_features(_bars(8))
    assert result.consecutive_expansion_days == 7
    assert result.consecutive_contraction_days == 0


def test_extreme_volume_spike_sets_risk_flag() -> None:
    bars = _bars(21)
    bars[-1] = MarketBar(**{**bars[-1].__dict__, "volume": 10_000})
    assert (
        CapitalFlowRiskFlag.EXTREME_VOLUME_SPIKE
        in calculate_volume_features(bars).risk_flags
    )


def test_suspension_does_not_enter_normal_average() -> None:
    bars = _bars(21)
    bars[-2] = MarketBar(**{**bars[-2].__dict__, "amount": 0, "volume": 0})
    result = calculate_amount_features(bars)
    assert result.average_amount_20d is None


# C. Turnover and price-volume
def test_turnover_uses_reliable_float_shares() -> None:
    bar = _bars(1, float_shares=1_000)[0]
    assert trusted_turnover(bar) == pytest.approx((bar.volume or 0) / 1_000)


def test_missing_float_shares_never_uses_total_shares() -> None:
    bar = _bars(1)[0]
    assert trusted_turnover(bar) is None


def test_high_turnover_sets_risk_flag() -> None:
    result = calculate_turnover_features(
        _bars(2, turnover=0.30),
        high_threshold=0.20,
    )
    assert CapitalFlowRiskFlag.HIGH_TURNOVER in result.risk_flags


@pytest.mark.parametrize(
    ("price_multiplier", "volume_multiplier", "expected"),
    [
        (1.02, 1.2, PriceVolumeState.PRICE_UP_VOLUME_UP),
        (1.02, 0.8, PriceVolumeState.PRICE_UP_VOLUME_DOWN),
        (0.98, 1.2, PriceVolumeState.PRICE_DOWN_VOLUME_UP),
        (0.98, 0.8, PriceVolumeState.PRICE_DOWN_VOLUME_DOWN),
    ],
)
def test_four_basic_price_volume_states(
    price_multiplier: float,
    volume_multiplier: float,
    expected: PriceVolumeState,
) -> None:
    bars = _bars(2)
    bars[-1] = MarketBar(
        **{
            **bars[-1].__dict__,
            "close": (bars[-2].close or 0) * price_multiplier,
            "volume": (bars[-2].volume or 0) * volume_multiplier,
        }
    )
    state, _ = classify_price_volume(
        bars,
        price_change_threshold=0.005,
        volume_change_threshold=0.1,
    )
    assert state == expected


@pytest.mark.parametrize(
    ("volume_multiplier", "expected"),
    [
        (1.2, PriceVolumeState.PRICE_FLAT_VOLUME_UP),
        (0.8, PriceVolumeState.PRICE_FLAT_VOLUME_DOWN),
    ],
)
def test_flat_price_volume_states(
    volume_multiplier: float,
    expected: PriceVolumeState,
) -> None:
    bars = _bars(2)
    bars[-1] = MarketBar(
        **{
            **bars[-1].__dict__,
            "close": bars[-2].close,
            "volume": (bars[-2].volume or 0) * volume_multiplier,
        }
    )
    assert classify_price_volume(
        bars,
        price_change_threshold=0.005,
        volume_change_threshold=0.1,
    )[0] == expected


def test_price_down_volume_up_sets_risk() -> None:
    bars = _bars(2)
    bars[-1] = MarketBar(
        **{
            **bars[-1].__dict__,
            "close": (bars[-2].close or 0) * 0.95,
            "volume": (bars[-2].volume or 0) * 2,
        }
    )
    _, risks = classify_price_volume(
        bars,
        price_change_threshold=0.005,
        volume_change_threshold=0.1,
    )
    assert CapitalFlowRiskFlag.PRICE_DOWN_VOLUME_UP in risks


def test_missing_price_or_volume_returns_unknown() -> None:
    bars = _bars(2)
    bars[-1] = MarketBar(**{**bars[-1].__dict__, "close": None})
    assert classify_price_volume(
        bars,
        price_change_threshold=0.005,
        volume_change_threshold=0.1,
    )[0] == PriceVolumeState.UNKNOWN


# D. Financing and estimated flow
def test_missing_financing_data_does_not_raise() -> None:
    assert calculate_financing_features([]).trend == FinancingTrend.MISSING


def test_missing_financing_is_not_zero() -> None:
    result = calculate_financing_features([])
    assert result.financing_balance is None
    assert result.financing_buy_amount is None


@pytest.mark.parametrize(
    ("balances", "expected"),
    [
        ([1, 2, 3], FinancingTrend.RISING),
        ([3, 2, 1], FinancingTrend.FALLING),
        ([1, 2, 1], FinancingTrend.VOLATILE),
        ([1], FinancingTrend.STABLE),
    ],
)
def test_financing_trend_classification(
    balances: list[float],
    expected: FinancingTrend,
) -> None:
    records = [
        FinancingRecord(
            record_id=f"fin_{index}",
            symbol="600000.SH",
            event_time=START + timedelta(days=index),
            data_cutoff=START + timedelta(days=index, hours=1),
            financing_balance=value,
            financing_buy_amount=None,
            securities_lending_balance=None,
            source="test",
        )
        for index, value in enumerate(balances)
    ]
    assert calculate_financing_features(records).trend == expected


def test_estimated_flow_is_explicitly_flagged() -> None:
    snapshot = _snapshot(
        estimated_flow=EstimatedFlow(
            10,
            "vendor",
            "undisclosed estimate",
            0.9,
            ("cmr_600000.SH_1",),
        )
    )
    assert CapitalFlowRiskFlag.ESTIMATED_FLOW_ONLY in snapshot.risk_flags
    assert snapshot.estimated_flow["classification"] == "ESTIMATED_FLOW"
    assert snapshot.estimated_flow["confidence"] <= 0.25


def test_estimated_flow_is_not_the_only_score_input() -> None:
    plain = _snapshot()
    estimated = _snapshot(
        estimated_flow=EstimatedFlow(10, "vendor", "estimate", 0.2, ())
    )
    assert estimated.score == plain.score


def test_decision_mode_does_not_inject_estimated_flow() -> None:
    snapshot = _snapshot(mode=AnalysisMode.DECISION)
    assert snapshot.estimated_flow is None


# E. Sector and market
def test_sector_amount_aggregation_is_correct() -> None:
    grouped = {"A": _bars(2, symbol="A", sector="Tech"), "B": _bars(2, symbol="B", sector="Tech")}
    result = aggregate_sector(
        sector="Tech",
        bars_by_symbol=grouped,
        market_total_amount=sum(items[-1].amount or 0 for items in grouped.values()),
        expected_symbols={"A", "B"},
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=CUTOFF,
    )
    assert result is not None
    assert result.sector_total_amount == sum(items[-1].amount or 0 for items in grouped.values())


def test_partial_sector_is_flagged() -> None:
    result = aggregate_sector(
        sector="Tech",
        bars_by_symbol={"A": _bars(2, symbol="A", sector="Tech")},
        market_total_amount=1,
        expected_symbols={"A", "B"},
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=CUTOFF,
    )
    assert result is not None
    assert CapitalFlowRiskFlag.PARTIAL_SECTOR in result.risk_flags


def test_missing_sector_mapping_degrades_safely() -> None:
    assert aggregate_sector(
        sector="Tech",
        bars_by_symbol={"A": _bars(2, symbol="A")},
        market_total_amount=1,
        expected_symbols=set(),
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=CUTOFF,
    ) is None


def test_market_amount_aggregation_is_correct() -> None:
    grouped = {"A": _bars(2, symbol="A"), "B": _bars(2, symbol="B")}
    result = aggregate_market(
        bars_by_symbol=grouped,
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=CUTOFF,
    )
    assert result.market_total_amount == sum(items[-1].amount or 0 for items in grouped.values())


def test_top10_amount_concentration_is_correct() -> None:
    grouped = {str(i): _bars(2, symbol=str(i), amount_start=float(i + 1)) for i in range(20)}
    result = aggregate_market(
        bars_by_symbol=grouped,
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=CUTOFF,
    )
    latest = sorted((items[-1].amount or 0 for items in grouped.values()), reverse=True)
    assert result.amount_concentration_top10 == pytest.approx(sum(latest[:10]) / sum(latest))


def test_partial_market_universe_is_flagged() -> None:
    result = aggregate_market(
        bars_by_symbol={"A": _bars(2, symbol="A")},
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=CUTOFF,
    )
    assert result.partial_universe is True
    assert CapitalFlowRiskFlag.PARTIAL_UNIVERSE in result.risk_flags


def test_partial_sample_is_not_claimed_as_full_market() -> None:
    result = aggregate_market(
        bars_by_symbol={"A": _bars(2, symbol="A")},
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=CUTOFF,
    )
    assert result.sample_universe_size == 1
    assert result.confidence <= settings.capital_flow_partial_confidence_cap


def test_market_calculation_has_no_model_dependency() -> None:
    source = inspect.getsource(aggregate_market).casefold()
    assert "model" not in source
    assert "llm" not in source


# F. Scoring
def test_sub_score_range_is_enforced() -> None:
    with pytest.raises(ValidationError):
        SubScore(value=1.1, confidence=1, available=True)


def test_missing_subscore_is_not_in_denominator() -> None:
    result = combine_sub_scores(
        {
            "volume": SubScore(value=1, confidence=1, available=True),
            "amount": SubScore(value=None, confidence=0, available=False),
        }
    )
    assert result.score == 1


def test_neutral_and_missing_are_distinct() -> None:
    neutral = SubScore(value=0, confidence=1, available=True)
    missing = SubScore(value=None, confidence=0, available=False)
    assert neutral.available is True
    assert missing.available is False


def test_total_score_weighted_formula_is_correct() -> None:
    result = combine_sub_scores(
        {
            "volume": SubScore(value=1, confidence=1, available=True),
            "amount": SubScore(value=-1, confidence=1, available=True),
        }
    )
    assert result.score == pytest.approx(0)


@pytest.mark.parametrize("ratio", [0.01, 0.5, 1, 2, 100])
def test_ratio_score_is_bounded(ratio: float) -> None:
    result = ratio_score(ratio, 1)
    assert result.value is not None
    assert -1 <= result.value <= 1


def test_less_data_produces_lower_confidence() -> None:
    full = combine_sub_scores(
        {
            "volume": SubScore(value=1, confidence=1, available=True),
            "amount": SubScore(value=1, confidence=1, available=True),
        }
    )
    partial = combine_sub_scores(
        {
            "volume": SubScore(value=1, confidence=0.2, available=True),
            "amount": SubScore(value=None, confidence=0, available=False),
        }
    )
    assert partial.confidence < full.confidence


def test_single_component_contribution_is_bounded() -> None:
    result = combine_sub_scores(
        {
            "volume": SubScore(value=1, confidence=1, available=True),
            "amount": SubScore(value=-1, confidence=1, available=True),
            "turnover": SubScore(value=-1, confidence=1, available=True),
        }
    )
    assert -1 <= result.score <= 1


def test_model_cannot_override_local_score() -> None:
    source = inspect.getsource(combine_sub_scores).casefold()
    assert "model" not in source
    assert "llm" not in source


# G. Mode isolation
def test_screening_never_calls_llm() -> None:
    assert policy_for(AnalysisMode.SCREENING).allow_model_calls is False


def test_screening_has_no_per_symbol_fetch() -> None:
    policy = policy_for(AnalysisMode.SCREENING)
    assert policy.allow_per_symbol_fetch is False
    assert policy.allow_external_fetch is False


@pytest.mark.asyncio
async def test_screening_returns_candidates_with_missing_data() -> None:
    fake = SimpleNamespace(
        latest_symbol_snapshot=lambda **kwargs: None,
        market_bars=lambda **kwargs: {"600000.SH": _bars(2)},
    )
    service = CapitalFlowAnalysisService(
        repository=fake,
        factor_repository=SimpleNamespace(),
    )
    result = await service.analyze(
        CapitalFlowAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            data_cutoff=CUTOFF,
            symbol="600000.SH",
        )
    )
    assert result.candidates
    assert result.missing_fields


@pytest.mark.asyncio
async def test_screening_existing_snapshot_keeps_screening_identity() -> None:
    existing = _snapshot(mode=AnalysisMode.RESEARCH)
    fake = SimpleNamespace(
        latest_symbol_snapshot=lambda **kwargs: existing,
    )
    service = CapitalFlowAnalysisService(
        repository=fake,
        factor_repository=SimpleNamespace(),
    )
    result = await service.analyze(
        CapitalFlowAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            data_cutoff=CUTOFF,
            symbol=existing.symbol,
        )
    )
    assert result.analysis_mode == AnalysisMode.SCREENING
    assert result.result is None
    assert result.candidates[0].snapshot_id == existing.snapshot_id


def test_screening_does_not_persist_factor() -> None:
    assert policy_for(AnalysisMode.SCREENING).persist_factor is False


def test_screening_does_not_create_decision_packet() -> None:
    source = inspect.getsource(CapitalFlowAnalysisService.analyze)
    assert "DecisionPacket" not in source


def test_research_allows_deeper_local_data_access() -> None:
    policy = policy_for(AnalysisMode.RESEARCH)
    assert policy.allow_per_symbol_fetch is True
    assert policy.allow_external_fetch is True


def test_research_factor_is_always_shadow() -> None:
    service = object.__new__(CapitalFlowAnalysisService)
    factor = service.factor_from_snapshot(_snapshot(), persist=False)
    assert factor.shadow_mode is True
    assert factor.metadata["formal_strategy_weight"] == 0


def test_decision_is_strict_point_in_time() -> None:
    assert policy_for(AnalysisMode.DECISION).strict_point_in_time is True


def test_decision_filters_future_market_data() -> None:
    bars = _bars(22)
    cutoff = bars[-2].data_cutoff
    snapshot = aggregate_symbol(
        symbol=bars[0].symbol,
        bars=bars,
        analysis_mode=AnalysisMode.DECISION,
        data_cutoff=cutoff,
        market_amounts={bars[0].symbol: bars[-2].amount or 0},
        universe_complete=False,
    )
    assert bars[-1].canonical_record_id not in snapshot.evidence_ids


def test_decision_factor_is_always_shadow() -> None:
    service = object.__new__(CapitalFlowAnalysisService)
    factor = service.factor_from_snapshot(
        _snapshot(mode=AnalysisMode.DECISION),
        persist=False,
    )
    assert factor.shadow_mode is True


def test_capital_flow_does_not_change_formal_action_weights() -> None:
    source = inspect.getsource(_generate_strategy)
    assert "technical.score * 0.6" in source
    assert "fundamental.score * 0.4" in source
    assert "capital" not in source.casefold()


def test_capital_flow_does_not_change_hard_risk_veto() -> None:
    source = inspect.getsource(CapitalFlowAnalysisService).casefold()
    assert "risk_veto" not in source
    assert "vetoed" not in source


def test_three_mode_policies_are_separate() -> None:
    policies = [policy_for(mode) for mode in AnalysisMode]
    assert len({id(item) for item in policies}) == 3
    assert policies[0] != policies[1] != policies[2]


# H. Migration and audit
def test_migration_is_idempotent(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "migration.duckdb")) as connection:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False


def test_migration_preserves_raw_records(capital_db: Path) -> None:
    del capital_db
    with get_connection() as connection:
        before = connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0]
        apply_migration(connection)
        after = connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0]
    assert after == before


def test_migration_preserves_canonical_records(capital_db: Path) -> None:
    del capital_db
    _seed_canonical(count=1)
    with get_connection() as connection:
        before = connection.execute("SELECT COUNT(*) FROM canonical_market_records").fetchone()[0]
        apply_migration(connection)
        after = connection.execute("SELECT COUNT(*) FROM canonical_market_records").fetchone()[0]
    assert after == before


def test_migration_preserves_sentiment_and_policy_records(capital_db: Path) -> None:
    del capital_db
    with get_connection() as connection:
        before = [
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sentiment_event_analyses", "policy_news_event_analyses")
        ]
        apply_migration(connection)
        after = [
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sentiment_event_analyses", "policy_news_event_analyses")
        ]
    assert after == before


def test_factor_evidence_can_be_resolved(capital_db: Path) -> None:
    del capital_db
    _seed_canonical()
    repository = CapitalFlowRepository()
    bars = repository.market_bars(data_cutoff=CUTOFF)["600000.SH"]
    snapshot = repository.save_symbol_snapshot(
        aggregate_symbol(
            symbol="600000.SH",
            bars=bars,
            analysis_mode=AnalysisMode.RESEARCH,
            data_cutoff=CUTOFF,
            market_amounts={"600000.SH": bars[-1].amount or 0},
            universe_complete=False,
        )
    )
    assert FactorOutputRepository.resolve_evidence(snapshot.snapshot_id) is not None
    assert FactorOutputRepository.resolve_evidence(snapshot.evidence_ids[0]) is not None


def test_backfill_dry_run_does_not_write_database(
    capital_db: Path,
    tmp_path: Path,
) -> None:
    del capital_db
    _seed_canonical()
    args = build_parser().parse_args(
        ["--dry-run", "--report-path", str(tmp_path / "dry.json")]
    )
    before = _table_count("capital_flow_symbol_snapshots")
    report = run(args)
    assert _table_count("capital_flow_symbol_snapshots") == before
    assert report["mode"] == "DRY_RUN"


def _table_count(table: str) -> int:
    with get_connection() as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_backfill_apply_is_idempotent(
    capital_db: Path,
    tmp_path: Path,
) -> None:
    del capital_db
    _seed_canonical()
    args1 = build_parser().parse_args(
        ["--apply", "--report-path", str(tmp_path / "first.json")]
    )
    args2 = build_parser().parse_args(
        ["--apply", "--report-path", str(tmp_path / "second.json")]
    )
    run(args1)
    first = _table_count("capital_flow_symbol_snapshots")
    second_report = run(args2)
    assert _table_count("capital_flow_symbol_snapshots") == first
    assert second_report["created_snapshot_count"] == 0


def test_one_bad_symbol_does_not_break_batch() -> None:
    service = object.__new__(CapitalFlowAnalysisService)
    snapshots, _ = service.calculate_batch(
        bars_by_symbol={"GOOD": _bars(2, symbol="GOOD"), "BAD": []},
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=CUTOFF,
    )
    assert [item.symbol for item in snapshots] == ["GOOD"]


def test_decision_packet_immutability_code_is_unchanged() -> None:
    from trading.schemas import DecisionPacket

    assert DecisionPacket.model_config["frozen"] is True
    assert "DecisionPacket" not in inspect.getsource(CapitalFlowAnalysisService)


def test_no_live_or_broker_code_added() -> None:
    assert broker_import_issues() == []
    assert indirect_execution_issues() == []
    assert decision_to_real_order_issues() == []


def test_5000_symbol_screening_is_batch_and_sorted() -> None:
    service = object.__new__(CapitalFlowAnalysisService)
    universe = {
        f"{index:06d}.SZ": _bars(2, symbol=f"{index:06d}.SZ", amount_start=1_000 + index)
        for index in range(5000)
    }
    snapshots, performance = service.profile_screening(
        bars_by_symbol=universe,
        data_cutoff=CUTOFF,
    )
    assert len(snapshots) == 5000
    assert snapshots == sorted(snapshots, key=lambda item: (-item.score, item.symbol))
    assert performance.database_connection_count == 0
    assert performance.network_request_count == 0
    assert performance.model_call_count == 0
    assert performance.elapsed_seconds > 0
    assert performance.peak_memory_bytes > 0


def test_capital_flow_openapi_routes_are_registered() -> None:
    from router.api.app import app

    paths = app.openapi()["paths"]
    assert {
        "/v1/capital-flow/analyze",
        "/v1/capital-flow/symbols/{symbol}",
        "/v1/capital-flow/sectors/{sector}",
        "/v1/capital-flow/market",
        "/v1/capital-flow/evaluate",
    } <= set(paths)


def test_all_capital_factors_have_zero_formal_weight() -> None:
    service = object.__new__(CapitalFlowAnalysisService)
    factor = service.factor_from_snapshot(_snapshot(), persist=False)
    assert factor.factor_type.value == "CAPITAL_FLOW"
    assert factor.shadow_mode is True
    assert factor.metadata["formal_strategy_weight"] == 0
    assert factor.model_call_ids == []


def test_liquidity_neutral_is_not_missing() -> None:
    neutral = liquidity_score(LiquidityLevel.MEDIUM, 0.8)
    missing = liquidity_score(LiquidityLevel.UNKNOWN, 0)
    assert neutral.value == 0 and neutral.available
    assert missing.value is None and not missing.available


def test_strategy_result_is_independent_of_capital_snapshot() -> None:
    technical = Signal(
        role="technical_agent",
        score=0.4,
        confidence=0.8,
        summary="technical",
        evidence=["t"],
        risks=[],
    )
    fundamental = Signal(
        role="fundamental_agent",
        score=0.2,
        confidence=0.8,
        summary="fundamental",
        evidence=["f"],
        risks=[],
    )
    before = _generate_strategy(technical, fundamental)
    _snapshot()
    after = _generate_strategy(technical, fundamental)
    assert before == after


def test_analyze_request_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        CapitalFlowAnalyzeRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            data_cutoff=datetime(2026, 1, 1),
            scope=CapitalFlowScope.SYMBOL,
            symbol="600000.SH",
        )


def test_repository_strictly_excludes_future_rows(capital_db: Path) -> None:
    del capital_db
    _seed_canonical(count=3)
    cutoff = START + timedelta(days=1, hours=1)
    rows = CapitalFlowRepository().market_bars(
        data_cutoff=cutoff,
        symbols=["600000.SH"],
    )["600000.SH"]
    assert len(rows) == 2
    assert all(row.data_cutoff <= cutoff for row in rows)
