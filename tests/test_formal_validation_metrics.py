from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading.experiments.a_share_validation import AShareTradingConstraints
from trading.experiments.formal_strategy_validation import FormalReplayDecision
from trading.experiments.formal_validation_metrics import FormalValidationMetricsService
from trading.experiments.parameter_sensitivity import FutureBar, HistoricalParameterObservation
from trading.scanner.production_partition import CandidateLayer


def _decision(*, factors=None, stop=8.0):
    start = date(2025, 11, 3)
    factors = factors or [1.0] * 20
    bars = tuple(
        FutureBar(
            trade_date=start + timedelta(days=index),
            open=10.0, high=11.0, low=9.0, close=10.0 + index / 10,
            previous_close=10.0 if index == 0 else 10.0 + (index - 1) / 10,
            adjustment_factor=factors[index],
        )
        for index in range(20)
    )
    observation = HistoricalParameterObservation(
        symbol="000001.SZ", signal_date=date(2025, 10, 31),
        score=0.7, signal_close=10.0, atr14=1.0,
        entry_zone=(9.5, 10.5), future_bars=bars,
        csi300_returns_by_exit_date={bar.trade_date: 0.01 for bar in bars},
        point_in_time_valid=True, score_source="FORMAL_60_40",
    )
    return FormalReplayDecision(
        symbol="000001.SZ", signal_date=date(2025, 10, 31),
        layer=CandidateLayer.CORE, market_regime="RISING",
        technical_score=0.8, fundamental_score=0.6,
        formal_score=0.72, confidence=0.8, action="STRONG_BUY", data_status="FRESH",
        execution_status="立即执行", veto_triggered=False,
        frozen_entry_zone=(9.5, 10.5), frozen_preferred_zone=(9.5, 10.5),
        frozen_stop_loss_price=stop, target_position_ratio=0.18,
        recommended_batches=3, point_in_time_valid=True,
        missing_data=(), observation=observation,
        industry_returns_by_exit_date={bar.trade_date: 0.02 for bar in bars},
    )


def test_formal_metrics_use_decision_engine_position_and_charge_costs():
    result = FormalValidationMetricsService().compare([_decision()])
    assert result.actionable_count == 1
    assert result.after_constraints[5].sample_count == 1
    assert result.after_constraints[5].average_return < result.before_constraints[5].average_return
    assert result.after_constraints[5].csi300_excess_return is not None
    assert result.after_constraints[5].industry_excess_return is not None


def test_forward_adjustment_factor_removes_corporate_action_price_drop():
    decision = _decision(factors=[1.0] + [2.0] * 19, stop=None)
    bars = list(decision.observation.future_bars)
    bars[1] = FutureBar(
        trade_date=bars[1].trade_date, open=5.0, high=5.5, low=4.5,
        close=5.0, previous_close=10.0, adjustment_factor=2.0,
    )
    observation = decision.observation.__class__(
        **{**decision.observation.__dict__, "future_bars": tuple(bars)}
    )
    decision = decision.__class__(
        **{**decision.__dict__, "observation": observation}
    )
    plain = FormalValidationMetricsService._unconstrained_trade(decision, 2)
    assert plain.net_return == pytest.approx(0.0)


def test_t_plus_one_does_not_allow_same_day_stop_exit():
    decision = _decision(stop=9.5)
    plain = FormalValidationMetricsService._unconstrained_trade(decision, 1)
    constrained = FormalValidationMetricsService._constrained_trade(
        decision, 1, AShareTradingConstraints()
    )
    assert plain.exit_date == decision.observation.future_bars[0].trade_date
    assert constrained.exit_date == decision.observation.future_bars[1].trade_date

def test_candidate_layer_outcome_is_independent_of_formal_trade_action():
    decision = _decision()
    decision = decision.__class__(
        **{
            **decision.__dict__,
            "action": "WAIT",
            "point_in_time_valid": False,
        }
    )
    result = FormalValidationMetricsService().candidate_outcomes_by_layer(
        [decision]
    )
    assert result["CORE"][20].sample_count == 1