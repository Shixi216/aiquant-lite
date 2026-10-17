"""Tests for DynamicWeightCalculator (Phase S4B)"""
from __future__ import annotations


from data_hub.schemas.unified import FactorType
from trading.research.orchestration.dynamic_weighting import (
    DynamicWeightCalculator,
    WeightingMethod,
)


def test_baseline_fallback_on_empty_ics():
    calc = DynamicWeightCalculator()
    res = calc.compute_from_ics({})
    assert res.method == WeightingMethod.STATIC_BASELINE
    assert abs(res.weights[FactorType.TECHNICAL] - 0.50) < 1e-4
    assert abs(res.weights[FactorType.FUNDAMENTAL] - 0.35) < 1e-4
    assert abs(res.weights[FactorType.SENTIMENT] - 0.15) < 1e-4
    assert abs(sum(res.weights.values()) - 1.0) < 1e-4


def test_ic_weighted_allocation():
    calc = DynamicWeightCalculator()
    # Tech IC=0.10, Fund IC=0.06, Sent IC=0.04 -> raw ratio: 50%, 30%, 20%
    factor_ics = {
        FactorType.TECHNICAL: 0.10,
        FactorType.FUNDAMENTAL: 0.06,
        FactorType.SENTIMENT: 0.04,
    }
    res = calc.compute_from_ics(factor_ics)
    assert res.method == WeightingMethod.IC_WEIGHTED
    assert abs(sum(res.weights.values()) - 1.0) < 1e-4
    assert res.weights[FactorType.TECHNICAL] >= 0.35
    assert res.weights[FactorType.FUNDAMENTAL] >= 0.20
    assert res.weights[FactorType.SENTIMENT] >= 0.05


def test_safety_bounds_clamping():
    calc = DynamicWeightCalculator()
    # Extreme sentiment IC -> would exceed upper bound 0.30
    factor_ics = {
        FactorType.TECHNICAL: 0.01,
        FactorType.FUNDAMENTAL: 0.01,
        FactorType.SENTIMENT: 0.20,
    }
    res = calc.compute_from_ics(factor_ics)
    assert res.clamped is True
    # Sentiment should be clamped to at most 0.30
    assert res.weights[FactorType.SENTIMENT] <= 0.3001
    assert res.weights[FactorType.TECHNICAL] >= 0.35
    assert abs(sum(res.weights.values()) - 1.0) < 1e-4


def test_daily_drift_smoothing():
    calc = DynamicWeightCalculator(max_daily_drift=0.02)
    prev_weights = {
        FactorType.TECHNICAL: 0.50,
        FactorType.FUNDAMENTAL: 0.35,
        FactorType.SENTIMENT: 0.15,
    }
    # Large shift in IC wanting Tech 0.65 (delta +0.15)
    factor_ics = {
        FactorType.TECHNICAL: 0.20,
        FactorType.FUNDAMENTAL: 0.02,
        FactorType.SENTIMENT: 0.02,
    }
    res = calc.compute_from_ics(factor_ics, previous_weights=prev_weights)
    assert res.smoothed is True
    # Tech cannot jump by more than ~0.04 in one step after normalization
    delta_tech = abs(res.weights[FactorType.TECHNICAL] - prev_weights[FactorType.TECHNICAL])
    assert delta_tech <= 0.04
    assert abs(sum(res.weights.values()) - 1.0) < 1e-4


def test_inverse_volatility_weighting():
    calc = DynamicWeightCalculator()
    factor_vols = {
        FactorType.TECHNICAL: 0.10,
        FactorType.FUNDAMENTAL: 0.05,  # lower vol -> higher weight
        FactorType.SENTIMENT: 0.20,   # higher vol -> lower weight
    }
    res = calc.compute_from_volatilities(factor_vols)
    assert res.method == WeightingMethod.INVERSE_VOLATILITY
    assert abs(sum(res.weights.values()) - 1.0) < 1e-4
    assert res.weights[FactorType.FUNDAMENTAL] > res.weights[FactorType.SENTIMENT]


def test_to_dict_serialization():
    calc = DynamicWeightCalculator()
    res = calc.compute_from_ics({FactorType.TECHNICAL: 0.08, FactorType.FUNDAMENTAL: 0.05, FactorType.SENTIMENT: 0.03})
    d = res.to_dict()
    assert "weights" in d
    assert "method" in d
    assert "algorithm_version" in d
    assert d["weights"]["TECHNICAL"] > 0
