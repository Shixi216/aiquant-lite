from __future__ import annotations

import inspect
from datetime import date, timedelta

from trading.experiments.parameter_sensitivity import (
    FutureBar,
    HistoricalParameterObservation,
    ParameterScenario,
    ParameterSensitivityService,
    default_parameter_scenarios,
)


def _observation(*, score: float = 0.7, falling: bool = False):
    bars = []
    for index in range(20):
        close = 105 - index if falling else 101 + index
        bars.append(
            FutureBar(
                trade_date=date(2026, 1, 2) + timedelta(days=index),
                open=100.5 if index == 0 else close,
                low=close - 1,
                close=close,
            )
        )
    return HistoricalParameterObservation(
        symbol="600000.SH",
        signal_date=date(2026, 1, 1),
        score=score,
        signal_close=100,
        atr14=2,
        entry_zone=(99, 103),
        future_bars=tuple(bars),
        csi300_returns={5: 0.01, 10: 0.02, 20: 0.03},
    )


def test_current_production_values_are_present_but_never_updated():
    scenarios = default_parameter_scenarios()
    current = {item.scenario_id: item for item in scenarios}
    assert current["threshold-current"].buy_threshold == 0.40
    assert current["threshold-current"].strong_buy_threshold == 0.65
    assert current["preferred-zone-40pct"].preferred_zone_width == 0.40
    assert current["atr-stop-2.0"].atr_stop_multiplier == 2.0
    result = ParameterSensitivityService().evaluate(
        [_observation()], current["baseline-current"]
    )
    assert result.production_config_updated is False
    assert result.orders_created == 0
    assert result.positions_changed == 0


def test_required_metrics_and_horizons_are_reported():
    result = ParameterSensitivityService().evaluate(
        [_observation(), _observation(falling=True)],
        ParameterScenario("test", "BASELINE"),
    )
    assert set(result.horizons) == {1, 3, 5, 10, 20}
    assert result.sample_count == 2
    assert result.win_rate == 0.5
    assert result.average_return is not None
    assert result.maximum_drawdown is not None
    assert result.profit_loss_ratio is not None
    assert result.horizons[5].csi300_excess_return is not None


def test_atr_stop_uses_future_low_only_after_next_open_entry():
    observation = _observation(falling=True)
    scenario = ParameterScenario(
        "tight-stop", "ATR_STOP_MULTIPLIER", atr_stop_multiplier=1.0
    )
    result = ParameterSensitivityService()._return_for(
        observation, scenario, 10
    )
    assert result is not None
    gross_return, _ = result
    assert gross_return == 98 / 100.5 - 1


def test_threshold_and_preferred_zone_filter_samples_independently():
    observation = _observation(score=0.42)
    service = ParameterSensitivityService()
    assert service.evaluate(
        [observation], ParameterScenario("low", "ACTION", buy_threshold=0.40)
    ).sample_count == 1
    assert service.evaluate(
        [observation], ParameterScenario("high", "ACTION", buy_threshold=0.45)
    ).sample_count == 0
    assert service.evaluate(
        [observation],
        ParameterScenario("narrow", "ZONE", preferred_zone_width=0.30),
    ).sample_count == 0


def test_missing_csi300_is_explicit_not_replaced_by_universe_proxy():
    observation = _observation()
    observation = HistoricalParameterObservation(
        **{
            **observation.__dict__,
            "csi300_returns": {},
        }
    )
    result = ParameterSensitivityService().evaluate(
        [observation], ParameterScenario("test", "BASELINE")
    )
    assert result.benchmark_available is False
    assert result.horizons[20].csi300_excess_return is None
    assert "CSI300_BENCHMARK_UNAVAILABLE" in result.risk_flags


def test_historical_technical_proxy_is_never_presented_as_formal_score():
    observation = HistoricalParameterObservation(
        **{
            **_observation().__dict__,
            "score_source": "TECHNICAL_PROXY_HISTORICAL_BAR_ONLY",
        }
    )
    result = ParameterSensitivityService().evaluate(
        [observation], ParameterScenario("test", "BASELINE")
    )
    assert "FORMAL_SCORE_UNAVAILABLE" in result.risk_flags
    assert "TECHNICAL_PROXY_ONLY" in result.risk_flags


def test_module_has_no_execution_or_production_config_write_path():
    source = inspect.getsource(__import__(
        "trading.experiments.parameter_sensitivity",
        fromlist=["parameter_sensitivity"],
    ))
    assert "submit_order" not in source
    assert "PaperTrading" not in source
    assert "manual_tracking" not in source
    assert "UPDATE config" not in source
