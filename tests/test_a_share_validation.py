from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest

from trading.experiments.a_share_validation import (
    AShareConstraintValidationService,
    AShareTradingConstraints,
)
from trading.experiments.parameter_sensitivity import (
    FutureBar,
    HistoricalParameterObservation,
    ParameterScenario,
    ParameterSensitivityService,
    _aligned_benchmark_returns,
    _portfolio_drawdown,
    load_historical_parameter_observations,
)


def _bars(*, flat: bool = False) -> tuple[FutureBar, ...]:
    output = []
    previous = 100.0
    for index in range(21):
        close = 100.0 if flat else 100.0 + index
        output.append(
            FutureBar(
                trade_date=date(2026, 1, 2) + timedelta(days=index),
                open=100.0 if index == 0 else close,
                high=close + 1,
                low=close - 1,
                close=close,
                previous_close=previous,
                volume=1_000_000,
            )
        )
        previous = close
    return tuple(output)


def _observation(
    *,
    bars: tuple[FutureBar, ...] | None = None,
    regime: str = "SIDEWAYS",
) -> HistoricalParameterObservation:
    values = bars or _bars()
    entry_date = values[0].trade_date
    entry_open = float(values[0].open or 100)
    return HistoricalParameterObservation(
        symbol="600000.SH",
        signal_date=date(2026, 1, 1),
        score=0.70,
        signal_close=100.0,
        atr14=20.0,
        entry_zone=(99.0, 103.0),
        future_bars=values,
        csi300_returns={1: 0.01, 3: 0.02, 5: 0.03, 10: 0.04, 20: 0.05},
        csi300_returns_by_exit_date={
            bar.trade_date: (float(bar.close or entry_open) / entry_open - 1)
            for bar in values
        },
        score_source="TECHNICAL_PROXY_HISTORICAL_BAR_ONLY",
        market_regime=regime,
        survivorship_bias_possible=True,
    )


def test_maximum_drawdown_uses_portfolio_curve_not_sequential_single_trades():
    signal_date = date(2026, 1, 1)
    rows = [
        (signal_date, "A", -0.50, 0.50, None),
        (signal_date, "B", -0.50, 0.50, None),
    ]
    assert _portfolio_drawdown(rows, 1) == pytest.approx(-0.50)


def test_t_plus_one_drawdown_uses_at_least_two_day_cohort_span():
    rows = [
        (date(2026, 1, 1), "A", 0.10, 1.0, None),
        (date(2026, 1, 2), "A", -0.90, 1.0, None),
        (date(2026, 1, 3), "A", -0.20, 1.0, None),
    ]
    metrics = AShareConstraintValidationService._metrics(
        1, rows, cohort_span=2
    )
    assert metrics.portfolio_maximum_drawdown == _portfolio_drawdown(rows, 2)


def test_t_plus_one_delays_one_day_horizon_exit():
    observation = _observation(bars=_bars(flat=True))
    scenario = ParameterScenario("baseline", "BASELINE")
    base = ParameterSensitivityService._return_for(observation, scenario, 1)
    assert base is not None
    trade = AShareConstraintValidationService._constrained_trade(
        observation,
        scenario,
        1,
        base,
        AShareTradingConstraints(),
    )
    assert trade.executed is True
    assert trade.exit_date == observation.future_bars[1].trade_date


def test_fees_stamp_duty_and_slippage_reduce_flat_trade_return():
    result = AShareConstraintValidationService().compare(
        [_observation(bars=_bars(flat=True))],
        ParameterScenario("baseline", "BASELINE"),
    )
    assert result.before[5].average_return == 0
    assert result.after[5].average_return is not None
    assert result.after[5].average_return < 0


def test_limit_up_entry_is_not_executable():
    bars = list(_bars())
    bars[0] = FutureBar(
        trade_date=bars[0].trade_date,
        open=110.0,
        high=111.0,
        low=109.5,
        close=110.0,
        previous_close=100.0,
        volume=1_000_000,
    )
    trade = AShareConstraintValidationService._constrained_trade(
        _observation(bars=tuple(bars)),
        ParameterScenario("baseline", "BASELINE"),
        5,
        (0.1, 0.04),
        AShareTradingConstraints(),
    )
    assert trade.executed is False
    assert trade.blocked_reason == "LIMIT_UP_ENTRY"


def test_one_price_entry_is_not_executable():
    bars = list(_bars())
    bars[0] = FutureBar(
        trade_date=bars[0].trade_date,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        previous_close=99.0,
        volume=1_000_000,
    )
    trade = AShareConstraintValidationService._constrained_trade(
        _observation(bars=tuple(bars)),
        ParameterScenario("baseline", "BASELINE"),
        5,
        (0.1, 0.04),
        AShareTradingConstraints(),
    )
    assert trade.executed is False
    assert trade.blocked_reason == "ONE_PRICE_ENTRY"


def test_limit_down_exit_is_deferred_to_next_executable_day():
    bars = list(_bars(flat=True))
    bars[1] = FutureBar(
        trade_date=bars[1].trade_date,
        open=90.0,
        high=91.0,
        low=89.0,
        close=90.0,
        previous_close=100.0,
        volume=1_000_000,
    )
    bars[2] = FutureBar(
        trade_date=bars[2].trade_date,
        open=95.0,
        high=96.0,
        low=94.0,
        close=95.0,
        previous_close=90.0,
        volume=1_000_000,
    )
    trade = AShareConstraintValidationService._constrained_trade(
        _observation(bars=tuple(bars)),
        ParameterScenario("baseline", "BASELINE"),
        1,
        (0.1, 0.04),
        AShareTradingConstraints(),
    )
    assert trade.executed is True
    assert trade.exit_date == bars[2].trade_date


def test_suspended_exit_is_deferred():
    bars = list(_bars(flat=True))
    bars[1] = FutureBar(
        trade_date=bars[1].trade_date,
        open=None,
        high=None,
        low=None,
        close=None,
        previous_close=100.0,
        suspended=True,
    )
    trade = AShareConstraintValidationService._constrained_trade(
        _observation(bars=tuple(bars)),
        ParameterScenario("baseline", "BASELINE"),
        1,
        (0.1, 0.04),
        AShareTradingConstraints(),
    )
    assert trade.executed is True
    assert trade.exit_date == bars[2].trade_date


def test_benchmark_is_selected_by_actual_deferred_exit_date():
    bars = list(_bars(flat=True))
    bars[1] = FutureBar(
        trade_date=bars[1].trade_date,
        open=None,
        high=None,
        low=None,
        close=None,
        previous_close=100.0,
        suspended=True,
    )
    observation = _observation(bars=tuple(bars))
    observation = HistoricalParameterObservation(
        **{
            **observation.__dict__,
            "csi300_returns_by_exit_date": {
                bars[1].trade_date: 0.01,
                bars[2].trade_date: 0.07,
            },
        }
    )
    trade = AShareConstraintValidationService._constrained_trade(
        observation,
        ParameterScenario("baseline", "BASELINE"),
        1,
        (0.1, 0.04),
        AShareTradingConstraints(),
    )
    assert trade.benchmark_return == 0.07


def test_comparison_is_research_only_and_never_writes_trade_state():
    result = AShareConstraintValidationService().compare(
        [_observation()],
        ParameterScenario("baseline", "BASELINE"),
    )
    assert result.technical_proxy_only is True
    assert result.production_config_updated is False
    assert result.orders_created == 0
    assert result.positions_changed == 0
    assert set(result.before) == {1, 3, 5, 10, 20}


def test_regime_slices_are_kept_separate():
    service = AShareConstraintValidationService()
    result = service.compare_by_regime(
        [
            _observation(regime="RISING"),
            _observation(regime="FALLING"),
            _observation(regime="SIDEWAYS"),
        ],
        ParameterScenario("baseline", "BASELINE"),
    )
    assert set(result) == {"RISING", "FALLING", "SIDEWAYS"}


def test_benchmark_horizons_use_exact_future_market_dates():
    signal_date = date(2026, 1, 1)
    future_dates = [date(2026, 1, day) for day in (2, 3, 4, 5, 6)]
    csi = {
        signal_date: (99.0, 100.0),
        future_dates[0]: (100.0, 101.0),
        future_dates[2]: (102.0, 103.0),
        future_dates[4]: (104.0, 105.0),
    }
    by_horizon, by_date = _aligned_benchmark_returns(
        signal_date, future_dates, csi
    )
    assert by_horizon[1] == pytest.approx(0.01)
    assert by_horizon[3] == pytest.approx(0.03)
    assert by_horizon[5] == pytest.approx(0.05)
    assert future_dates[1] not in by_date


def test_loader_rejects_rows_not_available_at_signal_cutoff():
    source = inspect.getsource(load_historical_parameter_observations)
    assert "row_cutoff > signal_close" in source
    assert "available > signal_close" in source
    assert "event > signal_close" in source


def test_future_bars_begin_after_signal_date():
    observation = _observation()
    assert all(
        bar.trade_date > observation.signal_date
        for bar in observation.future_bars
    )
