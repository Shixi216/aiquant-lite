from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading.experiments.parameter_sensitivity import FutureBar
from trading.experiments.timeline_quality_validation import (
    TimelineConstraintValidationService,
    TimelineTradeSample,
    distribution_diagnostics,
    summarize_results,
)


def _bar(day: date, price: float, previous: float, *, one_price: bool = False):
    spread = 0 if one_price else .2
    return FutureBar(
        trade_date=day, open=price, high=price + spread,
        low=price - spread, close=price, previous_close=previous,
        volume=1000,
    )


def _sample(prices: list[float], *, symbol: str = "000001.SZ", one_price=()):
    start = date(2025, 1, 2)
    bars = tuple(
        _bar(
            start + timedelta(days=index), value,
            prices[index - 1] if index else 10.0,
            one_price=index in one_price,
        )
        for index, value in enumerate(prices)
    )
    benchmark = {bar.trade_date: 100 + i for i, bar in enumerate(bars)}
    return TimelineTradeSample(
        packet_id="packet", symbol=symbol, signal_date=start,
        entry_date=start, entry_day=1, market_regime="SIDEWAYS",
        core_structure_at_entry=True, bars=bars,
        csi300_close_by_date=benchmark,
        industry_close_by_date=benchmark,
    )


def test_costs_and_t_plus_one_use_next_day_exit():
    result = TimelineConstraintValidationService.evaluate(
        _sample([10.0, 11.0, 12.0]), 1
    )
    assert result.executed
    assert result.exit_date == date(2025, 1, 3)
    assert result.gross_return == pytest.approx(.10)
    assert result.net_return < result.gross_return


def test_limit_up_entry_is_blocked():
    sample = _sample([11.0, 11.2, 11.3])
    result = TimelineConstraintValidationService.evaluate(sample, 1)
    assert not result.executed
    assert result.blocked_reason == "LIMIT_UP_ENTRY"
    baseline = TimelineConstraintValidationService.evaluate_unconstrained(sample, 1)
    assert baseline.executed
    assert baseline.gross_return == pytest.approx(11.2 / 11.0 - 1)


def test_one_price_entry_is_blocked():
    result = TimelineConstraintValidationService.evaluate(
        _sample([10.0, 10.1], one_price=(0,)), 1
    )
    assert not result.executed
    assert result.blocked_reason == "ONE_PRICE_ENTRY"


def test_limit_down_exit_is_delayed():
    result = TimelineConstraintValidationService.evaluate(
        _sample([10.0, 9.0, 9.2, 9.3]), 1
    )
    assert result.executed
    assert result.exit_date == date(2025, 1, 4)
    assert result.exit_delay_days == 1
    summary = summarize_results([result], constrained=True)
    assert summary["delayed_exit_count"] == 1


def test_distribution_removes_top_winners():
    result = distribution_diagnostics([-1.0, 0.0, 1.0, 10.0] * 25)
    assert result["sample_count"] == 100
    assert result["top_10_percent"]["count"] == 10
    assert result["top_10_percent"]["average_without_top"] < 2.5

