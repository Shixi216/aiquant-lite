from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import duckdb
import pytest

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_EVALUATION_SHA,
    EXPECTED_LABEL_SPEC_SHA,
    EXPECTED_REGISTRY_SHA,
)
from trading.research.sentiment.v3.feature_correlation import (
    calculate_feature_correlations,
)
from trading.research.sentiment.v3.multiple_testing import benjamini_hochberg
from trading.research.sentiment.v3.s3b2_runner import (
    FeatureStability,
    HorizonStability,
    S3B2ResearchRunner,
    YearMetric,
    _sanitize,
    classify_feature_stability,
    classify_horizon,
    classify_window,
    equal_weight_score_expression,
)


def _correlation_database(path: Path) -> list[str]:
    features = [
        "market.advancers",
        "market.decliners",
        "stock.return_pct",
        "stock.intraday_return_pct",
    ]
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE replay_feature_packets (
            trade_date DATE,
            symbol VARCHAR,
            record_source VARCHAR,
            quality_flags_json JSON,
            market__advancers DOUBLE,
            market__decliners DOUBLE,
            stock__return_pct DOUBLE,
            stock__intraday_return_pct DOUBLE
        )
        """
    )
    rows = []
    for day in range(1, 31):
        for symbol_index in range(3):
            stock_value = day * 0.1 + symbol_index
            rows.append((
                f"2025-01-{day:02d}", f"S{symbol_index}", "REPLAY", "[]", day, 100 - day,
                stock_value, stock_value * 2,
            ))
    connection.executemany("INSERT INTO replay_feature_packets VALUES (?,?,?,?,?,?,?,?)", rows)
    connection.close()
    return features


def test_01_feature_correlation_detects_high_pairs_without_deleting(tmp_path: Path):
    database = tmp_path / "correlation.duckdb"
    features = _correlation_database(database)
    result = calculate_feature_correlations(database, features)
    assert len(result.pairs) == 6
    high = {
        (item.left_feature, item.right_feature) for item in result.highly_correlated_pairs
    }
    assert ("stock.return_pct", "stock.intraday_return_pct") in high
    grouped = {item for family in result.families for item in family.members}
    assert grouped == set(features)


def test_02_bh_fdr_matches_reference_values():
    results = benjamini_hochberg([
        ("a", 0.01), ("b", 0.04), ("c", 0.03), ("d", 0.002)
    ])
    values = {item.test_id: item.q_value for item in results}
    assert values == pytest.approx({"a": 0.02, "b": 0.04, "c": 0.04, "d": 0.008})


def test_03_bh_fdr_keeps_missing_test_as_conservative_one():
    results = benjamini_hochberg([("known", 0.01), ("missing", None)])
    assert len(results) == 2
    missing = next(item for item in results if item.test_id == "missing")
    assert missing.raw_p_value == 1.0
    assert missing.q_value == 1.0
    assert not missing.rejected


def test_04_time_slice_classification_is_rule_based():
    years = [
        YearMetric(year=year, valid_dates=80, ic_mean=0.02,
                   rank_ic_mean=0.02, q5_q1_spread=0.01)
        for year in (2024, 2025, 2026)
    ]
    assert classify_horizon(years) == ("STABLE", "POSITIVE")
    mixed = [years[0], years[1], YearMetric(
        year=2026, valid_dates=80, ic_mean=-0.02,
        rank_ic_mean=-0.02, q5_q1_spread=-0.01,
    )]
    assert classify_horizon(mixed) == ("PERIOD_DEPENDENT", "MIXED")


def test_05_feature_stability_requires_multiple_horizons():
    years = tuple(YearMetric(
        year=year, valid_dates=80, ic_mean=0.02,
        rank_ic_mean=0.02, q5_q1_spread=0.01,
    ) for year in (2024, 2025, 2026))
    horizons = [
        HorizonStability(horizon=horizon, status="STABLE", direction="POSITIVE", years=years)
        for horizon in (1, 3, 5)
    ]
    result = classify_feature_stability("stock.return_pct", horizons)
    assert isinstance(result, FeatureStability)
    assert result.status == "STABLE"


def test_06_forward_window_does_not_select_only_best_horizon():
    assert classify_window([0.02, 0.03]) == "EFFECTIVE_POSITIVE"
    assert classify_window([0.02, -0.03]) == "DIRECTION_REVERSAL"
    assert classify_window([0.001, 0.002]) == "NOT_EFFECTIVE"


def test_07_equal_weight_combination_is_deterministic_and_missing_aware():
    expression = equal_weight_score_expression(["rank_a", "rank_b"], 2)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE ranks(rank_a DOUBLE,rank_b DOUBLE)")
    connection.execute("INSERT INTO ranks VALUES (0.2,0.8),(0.5,NULL)")
    first = connection.execute(f"SELECT {expression} FROM ranks ORDER BY rank_a").fetchall()
    second = connection.execute(f"SELECT {expression} FROM ranks ORDER BY rank_a").fetchall()
    connection.close()
    assert first == second
    assert first[0][0] == pytest.approx(0.5)
    assert first[1][0] is None


def test_08_pit_validation_rejects_future_available_time(tmp_path: Path):
    database = tmp_path / "pit.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        """
        CREATE TABLE replay_feature_packets (
            market_available_time TIMESTAMPTZ,
            stock_available_time TIMESTAMPTZ,
            margin_available_time TIMESTAMPTZ,
            dragon_tiger_available_time TIMESTAMPTZ,
            feature_trade_date DATE,
            decision_cutoff TIMESTAMPTZ
        )
        """
    )
    connection.execute(
        """
        INSERT INTO replay_feature_packets VALUES (
            '2025-01-03 09:20:00+08','2025-01-03 09:00:00+08',
            '2025-01-03 09:00:00+08','2025-01-02 18:00:00+08',
            '2025-01-02','2025-01-03 09:10:00+08'
        )
        """
    )
    connection.close()
    runner = S3B2ResearchRunner(replay_database=database)
    with pytest.raises(ValueError, match="PIT validation failed"):
        runner._validate_pit()


def test_09_frozen_contract_hashes_are_not_redefined():
    assert EXPECTED_REGISTRY_SHA == "779AD72DEC929AC42FC7A65FB5E3E7A94913C44E8F1010FC63B9181030466080"
    assert EXPECTED_LABEL_SPEC_SHA == "49D6BAB04E3548A7A57606B93A3E326797AA3981C7DD1DD296374FDC459EF1D0"
    assert EXPECTED_EVALUATION_SHA == "F2D3D2059B63FD4A5FBC6963A9F16EAEC9EBC49D47FEA89D48D94E67C4859138"


def test_10_research_models_have_no_trading_action_fields():
    metric = YearMetric(
        year=2025, valid_dates=10, ic_mean=0.01,
        rank_ic_mean=0.01, q5_q1_spread=0.01,
    )
    keys = set(asdict(metric))
    assert not keys & {"action", "signal", "target_position", "formal_score"}



def test_11_non_finite_statistics_remain_missing_not_zero():
    result = _sanitize({"nan": float("nan"), "inf": float("inf"), "value": 0.0})
    assert result == {"nan": None, "inf": None, "value": 0.0}
