from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading.experiments.portfolio_curve_validation import (
    PortfolioTrade,
    simulate_portfolio_curve,
)


def _trade(name: str, start: date, prices: list[float], weight: float):
    return PortfolioTrade(
        packet_id=name,
        symbol=name,
        entry_date=start,
        exit_date=start + timedelta(days=len(prices) - 1),
        requested_weight=weight,
        adjusted_close_by_date={
            start + timedelta(days=index): value
            for index, value in enumerate(prices)
        },
    )


def test_simultaneous_entries_are_scaled_to_available_cash():
    start = date(2025, 1, 2)
    result = simulate_portfolio_curve(
        [_trade("a", start, [10, 10], .6), _trade("b", start, [10, 10], .6)],
        [start, start + timedelta(days=1)],
    )
    assert result.cash_limited_trades == 2
    assert result.maximum_simultaneous_positions == 2
    assert result.maximum_gross_exposure <= 1.001
    assert result.minimum_cash_ratio >= -1e-12


def test_overlapping_positions_and_exit_cash_are_accounted_for():
    start = date(2025, 1, 2)
    result = simulate_portfolio_curve(
        [
            _trade("a", start, [10, 11, 12], .5),
            _trade("b", start + timedelta(days=1), [20, 20], .7),
        ],
        [start + timedelta(days=index) for index in range(3)],
    )
    assert result.maximum_simultaneous_positions == 2
    assert result.filled_trades == 2
    assert result.daily_nav[start + timedelta(days=2)] > .99


def test_portfolio_curve_enforces_t_plus_one():
    start = date(2025, 1, 2)
    trade = PortfolioTrade("a", "a", start, start, .1, {start: 10})
    with pytest.raises(ValueError, match=r"T\+1"):
        simulate_portfolio_curve([trade], [start])


def test_maximum_drawdown_comes_from_daily_nav():
    start = date(2025, 1, 2)
    result = simulate_portfolio_curve(
        [_trade("a", start, [10, 8, 9], 1.0)],
        [start + timedelta(days=index) for index in range(3)],
    )
    assert result.maximum_drawdown < -.19
    assert len(result.daily_nav) == 3
