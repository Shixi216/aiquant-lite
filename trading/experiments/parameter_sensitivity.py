from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from math import prod
from statistics import fmean, median
from time import perf_counter

from database.db import get_connection


CHINA_TZ = timezone(timedelta(hours=8))
PARAMETER_BACKTEST_VERSION = "parameter-sensitivity-v1"
REQUIRED_HORIZONS = (1, 3, 5, 10, 20)


@dataclass(frozen=True)
class FutureBar:
    trade_date: date
    open: float | None
    low: float | None
    close: float | None
    high: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    suspended: bool = False
    adjustment_factor: float | None = None


@dataclass(frozen=True)
class HistoricalParameterObservation:
    symbol: str
    signal_date: date
    score: float
    signal_close: float
    atr14: float
    entry_zone: tuple[float, float]
    future_bars: tuple[FutureBar, ...]
    csi300_returns: dict[int, float] = field(default_factory=dict)
    csi300_returns_by_exit_date: dict[date, float] = field(default_factory=dict)
    point_in_time_valid: bool = True
    score_source: str = "FORMAL_60_40"
    market_regime: str = "UNKNOWN"
    survivorship_bias_possible: bool = False


@dataclass(frozen=True)
class ParameterScenario:
    scenario_id: str
    dimension: str
    buy_threshold: float = 0.40
    strong_buy_threshold: float = 0.65
    preferred_zone_width: float = 0.40
    buy_target_position: float = 0.12
    strong_target_position: float = 0.18
    fixed_batches: int | None = None
    atr_stop_multiplier: float = 2.0


@dataclass(frozen=True)
class HorizonMetrics:
    horizon: int
    sample_count: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None
    maximum_drawdown: float | None
    profit_loss_ratio: float | None
    csi300_excess_return: float | None


@dataclass(frozen=True)
class ParameterBacktestResult:
    scenario: ParameterScenario
    sample_count: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None
    maximum_drawdown: float | None
    profit_loss_ratio: float | None
    horizons: dict[int, HorizonMetrics]
    benchmark_name: str
    benchmark_available: bool
    risk_flags: tuple[str, ...]
    elapsed_ms: float
    production_config_updated: bool = False
    orders_created: int = 0
    positions_changed: int = 0


def default_parameter_scenarios() -> tuple[ParameterScenario, ...]:
    """Independent sensitivity cases; none is written to production settings."""

    baseline = ParameterScenario(
        scenario_id="baseline-current",
        dimension="BASELINE",
    )
    return (
        baseline,
        replace(
            baseline,
            scenario_id="threshold-lower",
            dimension="ACTION_THRESHOLDS",
            buy_threshold=0.35,
            strong_buy_threshold=0.60,
        ),
        replace(
            baseline,
            scenario_id="threshold-current",
            dimension="ACTION_THRESHOLDS",
        ),
        replace(
            baseline,
            scenario_id="threshold-higher",
            dimension="ACTION_THRESHOLDS",
            buy_threshold=0.45,
            strong_buy_threshold=0.70,
        ),
        replace(
            baseline,
            scenario_id="preferred-zone-30pct",
            dimension="PREFERRED_ZONE_WIDTH",
            preferred_zone_width=0.30,
        ),
        replace(
            baseline,
            scenario_id="preferred-zone-40pct",
            dimension="PREFERRED_ZONE_WIDTH",
        ),
        replace(
            baseline,
            scenario_id="preferred-zone-50pct",
            dimension="PREFERRED_ZONE_WIDTH",
            preferred_zone_width=0.50,
        ),
        replace(
            baseline,
            scenario_id="position-conservative",
            dimension="POSITION_AND_BATCHES",
            buy_target_position=0.08,
            strong_target_position=0.12,
            fixed_batches=3,
        ),
        replace(
            baseline,
            scenario_id="position-current",
            dimension="POSITION_AND_BATCHES",
        ),
        replace(
            baseline,
            scenario_id="position-concentrated",
            dimension="POSITION_AND_BATCHES",
            buy_target_position=0.15,
            strong_target_position=0.20,
            fixed_batches=2,
        ),
        replace(
            baseline,
            scenario_id="atr-stop-1.5",
            dimension="ATR_STOP_MULTIPLIER",
            atr_stop_multiplier=1.5,
        ),
        replace(
            baseline,
            scenario_id="atr-stop-2.0",
            dimension="ATR_STOP_MULTIPLIER",
        ),
        replace(
            baseline,
            scenario_id="atr-stop-2.5",
            dimension="ATR_STOP_MULTIPLIER",
            atr_stop_multiplier=2.5,
        ),
    )


def _batches(target: float, fixed: int | None) -> int:
    if fixed is not None:
        return fixed
    if target <= 0.05:
        return 1
    if target <= 0.10:
        return 2
    return 3


def _drawdown(weighted_returns: list[float]) -> float | None:
    if not weighted_returns:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in weighted_returns:
        equity *= 1 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def _portfolio_drawdown(
    ordered: list[tuple[date, str, float, float, float | None]],
    horizon: int,
) -> float | None:
    """Drawdown of non-overlapping cohort portfolio NAV, not single trades."""

    grouped: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for signal_date, _, gross_return, exposure, _ in ordered:
        grouped[signal_date].append((gross_return, exposure))
    cohort_returns: list[float] = []
    for signal_date in sorted(grouped)[::horizon]:
        trades = grouped[signal_date]
        total_exposure = sum(exposure for _, exposure in trades)
        scale = 1.0 if total_exposure <= 1 else 1 / total_exposure
        cohort_returns.append(
            sum(
                gross_return * exposure * scale
                for gross_return, exposure in trades
            )
        )
    return _drawdown(cohort_returns)


def _aligned_benchmark_returns(
    signal_date: date,
    future_dates: list[date],
    csi: dict[date, tuple[float, float]],
) -> tuple[dict[int, float], dict[date, float]]:
    """Align benchmark entry and exits to the exact stock-market dates."""

    if signal_date not in csi or not future_dates or future_dates[0] not in csi:
        return {}, {}
    entry_open = csi[future_dates[0]][0]
    if entry_open <= 0:
        return {}, {}
    by_horizon = {
        horizon: csi[future_dates[horizon - 1]][1] / entry_open - 1
        for horizon in REQUIRED_HORIZONS
        if len(future_dates) >= horizon and future_dates[horizon - 1] in csi
    }
    by_date = {
        future_date: csi[future_date][1] / entry_open - 1
        for future_date in future_dates
        if future_date in csi
    }
    return by_horizon, by_date


def _profit_loss_ratio(values: list[float]) -> float | None:
    gains = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    if not gains or not losses:
        return None
    return fmean(gains) / abs(fmean(losses))


class ParameterSensitivityService:
    """Research-only sensitivity analysis over immutable historical observations."""

    @staticmethod
    def _return_for(
        observation: HistoricalParameterObservation,
        scenario: ParameterScenario,
        horizon: int,
    ) -> tuple[float, float] | None:
        if (
            not observation.point_in_time_valid
            or observation.score < scenario.buy_threshold
            or len(observation.future_bars) < horizon
        ):
            return None
        zone_low, zone_high = observation.entry_zone
        preferred_high = zone_low + scenario.preferred_zone_width * (
            zone_high - zone_low
        )
        entry = observation.future_bars[0].open
        if entry is None:
            return None
        if entry <= 0 or not zone_low <= entry <= preferred_high:
            return None
        target = (
            scenario.strong_target_position
            if observation.score >= scenario.strong_buy_threshold
            else scenario.buy_target_position
        )
        batches = _batches(target, scenario.fixed_batches)
        exposure = target / batches
        stop = observation.signal_close - (
            scenario.atr_stop_multiplier * observation.atr14
        )
        exit_price = observation.future_bars[horizon - 1].close
        if exit_price is None:
            return None
        for bar in observation.future_bars[:horizon]:
            if (
                bar.open is not None
                and bar.low is not None
                and stop > 0
                and bar.low <= stop
            ):
                exit_price = min(bar.open, stop)
                break
        return exit_price / entry - 1, exposure

    def evaluate(
        self,
        observations: list[HistoricalParameterObservation],
        scenario: ParameterScenario,
    ) -> ParameterBacktestResult:
        started = perf_counter()
        horizon_results: dict[int, HorizonMetrics] = {}
        benchmark_available = False
        for horizon in REQUIRED_HORIZONS:
            ordered: list[tuple[date, str, float, float, float | None]] = []
            for observation in observations:
                result = self._return_for(observation, scenario, horizon)
                if result is None:
                    continue
                gross_return, exposure = result
                benchmark = observation.csi300_returns.get(horizon)
                benchmark_available = benchmark_available or benchmark is not None
                ordered.append(
                    (
                        observation.signal_date,
                        observation.symbol,
                        gross_return,
                        exposure,
                        benchmark,
                    )
                )
            ordered.sort(key=lambda item: (item[0], item[1]))
            returns = [item[2] for item in ordered]
            excess = [
                item[2] - item[4]
                for item in ordered
                if item[4] is not None
            ]
            horizon_results[horizon] = HorizonMetrics(
                horizon=horizon,
                sample_count=len(returns),
                win_rate=(
                    None
                    if not returns
                    else sum(value > 0 for value in returns) / len(returns)
                ),
                average_return=None if not returns else fmean(returns),
                median_return=None if not returns else median(returns),
                maximum_drawdown=_portfolio_drawdown(ordered, horizon),
                profit_loss_ratio=_profit_loss_ratio(returns),
                csi300_excess_return=None if not excess else fmean(excess),
            )
        primary = horizon_results[20]
        flags = ["RESEARCH_ONLY", "PRODUCTION_CONFIG_UNCHANGED"]
        if not benchmark_available:
            flags.append("CSI300_BENCHMARK_UNAVAILABLE")
        if primary.sample_count < 30:
            flags.append("SAMPLE_TOO_SMALL")
        if any(
            item.score_source != "FORMAL_60_40"
            for item in observations
        ):
            flags.extend(
                ["FORMAL_SCORE_UNAVAILABLE", "TECHNICAL_PROXY_ONLY"]
            )
        if any(item.survivorship_bias_possible for item in observations):
            flags.append("SURVIVORSHIP_BIAS_RISK")
        return ParameterBacktestResult(
            scenario=scenario,
            sample_count=primary.sample_count,
            win_rate=primary.win_rate,
            average_return=primary.average_return,
            median_return=primary.median_return,
            maximum_drawdown=primary.maximum_drawdown,
            profit_loss_ratio=primary.profit_loss_ratio,
            horizons=horizon_results,
            benchmark_name="CSI300",
            benchmark_available=benchmark_available,
            risk_flags=tuple(flags),
            elapsed_ms=(perf_counter() - started) * 1000,
        )

    def evaluate_all(
        self,
        observations: list[HistoricalParameterObservation],
        scenarios: tuple[ParameterScenario, ...] | None = None,
    ) -> list[ParameterBacktestResult]:
        return [
            self.evaluate(observations, scenario)
            for scenario in (scenarios or default_parameter_scenarios())
        ]


def load_historical_parameter_observations(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    maximum_signals_per_day: int = 20,
    csi300_closes: dict[date, tuple[float, float]] | None = None,
) -> list[HistoricalParameterObservation]:
    """Build signals from information available by each signal-day close.

    ``csi300_closes`` maps a trading date to ``(open, close)``. It is optional;
    the loader never substitutes an equal-weight universe for CSI300.
    """

    if maximum_signals_per_day < 1:
        raise ValueError("maximum_signals_per_day must be positive")
    cutoff_clause = ""
    parameters: list[object] = []
    if start_date is not None:
        cutoff_clause += " AND trade_date >= ?"
        parameters.append(start_date - timedelta(days=60))
    if end_date is not None:
        cutoff_clause += " AND trade_date <= ?"
        parameters.append(end_date + timedelta(days=40))
    query = f"""
        SELECT symbol, trade_date, open, high, low, close, volume,
               data_available_time, event_time, data_cutoff
        FROM canonical_historical_bars
        WHERE adjustment_type = 'RAW'
          AND verification_status <> 'CONFLICT'
          {cutoff_clause}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol, trade_date
            ORDER BY generated_at DESC, bar_id
        ) = 1
        ORDER BY symbol, trade_date
    """
    with get_connection(read_only=True) as connection:
        rows = connection.execute(query, parameters).fetchall()

    grouped: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        (
            symbol, trade_date, open_, high, low, close, volume,
            available, event, row_cutoff,
        ) = row
        signal_close = datetime.combine(trade_date, time(16), tzinfo=CHINA_TZ)
        if (
            available is None
            or event is None
            or row_cutoff is None
            or any(value is None for value in (open_, high, low, close, volume))
        ):
            continue
        if (
            available > signal_close
            or event > signal_close
            or row_cutoff > signal_close
        ):
            continue
        if min(float(open_), float(high), float(low), float(close)) <= 0:
            continue
        grouped[str(symbol)].append(row)

    market_dates = sorted(
        {row[1] for symbol_rows in grouped.values() for row in symbol_rows}
    )
    market_date_index = {value: index for index, value in enumerate(market_dates)}
    bars_by_symbol_date = {
        symbol: {row[1]: row for row in symbol_rows}
        for symbol, symbol_rows in grouped.items()
    }

    raw_signals: dict[date, list[dict[str, object]]] = defaultdict(list)
    for symbol, bars in grouped.items():
        for index in range(20, len(bars)):
            signal = bars[index]
            trade_date = signal[1]
            calendar_offset = market_date_index[trade_date]
            if calendar_offset + 21 >= len(market_dates):
                continue
            if start_date is not None and trade_date < start_date:
                continue
            if end_date is not None and trade_date > end_date:
                continue
            history = bars[index - 19 : index + 1]
            closes = [float(item[5]) for item in history]
            volumes = [float(item[6]) for item in history]
            previous_high = max(float(item[3]) for item in bars[index - 20 : index])
            true_ranges = []
            for offset in range(index - 13, index + 1):
                current = bars[offset]
                previous_close = float(bars[offset - 1][5])
                true_ranges.append(
                    max(
                        float(current[3]) - float(current[4]),
                        abs(float(current[3]) - previous_close),
                        abs(float(current[4]) - previous_close),
                    )
                )
            close = float(signal[5])
            raw_signals[trade_date].append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "close": close,
                    "momentum": close / closes[0] - 1,
                    "change": close / float(bars[index - 1][5]) - 1,
                    "volume_ratio": float(signal[6]) / max(fmean(volumes), 1),
                    "breakout": close / previous_high - 1,
                    "amount_proxy": close * float(signal[6]),
                    "atr14": fmean(true_ranges),
                    "support": min(float(item[4]) for item in history),
                }
            )

    observations: list[HistoricalParameterObservation] = []
    csi = csi300_closes or {}
    csi_dates = sorted(csi)
    csi_index = {value: index for index, value in enumerate(csi_dates)}
    for signal_date, values in raw_signals.items():
        if len(values) < 2:
            continue
        for key in ("momentum", "change", "volume_ratio", "breakout", "amount_proxy"):
            ordered = sorted(values, key=lambda item: (float(item[key]), str(item["symbol"])))
            denominator = max(len(ordered) - 1, 1)
            for rank, item in enumerate(ordered):
                item[key + "_rank"] = rank / denominator
        for item in values:
            item["score"] = (
                float(item["momentum_rank"]) * 0.50
                + float(item["change_rank"]) * 0.20
                + float(item["volume_ratio_rank"]) * 0.15
                + float(item["breakout_rank"]) * 0.15
            )
        selected = sorted(
            values,
            key=lambda item: (-float(item["score"]), str(item["symbol"])),
        )[:maximum_signals_per_day]
        for item in selected:
            close = float(item["close"])
            support = float(item["support"])
            entry_low = max(support, close * 0.95)
            entry_high = close * 1.01
            if entry_low > entry_high:
                entry_low = close * 0.95
            offset = market_date_index[signal_date]
            future_dates = market_dates[offset + 1 : offset + 22]
            future_values: list[FutureBar] = []
            reference_close = close
            symbol_rows = bars_by_symbol_date[str(item["symbol"])]
            for future_date in future_dates:
                row = symbol_rows.get(future_date)
                if row is None:
                    future_values.append(
                        FutureBar(
                            trade_date=future_date,
                            open=None,
                            low=None,
                            close=None,
                            previous_close=reference_close,
                            suspended=True,
                        )
                    )
                    continue
                future_values.append(
                    FutureBar(
                        trade_date=future_date,
                        open=float(row[2]),
                        high=float(row[3]),
                        low=float(row[4]),
                        close=float(row[5]),
                        previous_close=reference_close,
                        volume=float(row[6]),
                    )
                )
                reference_close = float(row[5])
            future = tuple(future_values)
            benchmark, benchmark_by_date = _aligned_benchmark_returns(
                signal_date,
                future_dates,
                csi,
            )
            index = csi_index.get(signal_date)
            market_regime = "UNKNOWN"
            if index is not None and index >= 20:
                trailing_return = (
                    csi[csi_dates[index]][1] / csi[csi_dates[index - 20]][1] - 1
                )
                market_regime = (
                    "RISING"
                    if trailing_return >= 0.05
                    else "FALLING"
                    if trailing_return <= -0.05
                    else "SIDEWAYS"
                )
            observations.append(
                HistoricalParameterObservation(
                    symbol=str(item["symbol"]),
                    signal_date=signal_date,
                    score=float(item["score"]),
                    signal_close=close,
                    atr14=float(item["atr14"]),
                    entry_zone=(entry_low, entry_high),
                    future_bars=future,
                    csi300_returns=benchmark,
                    csi300_returns_by_exit_date=benchmark_by_date,
                    score_source="TECHNICAL_PROXY_HISTORICAL_BAR_ONLY",
                    market_regime=market_regime,
                    survivorship_bias_possible=True,
                )
            )
    return sorted(observations, key=lambda item: (item.signal_date, item.symbol))


__all__ = [
    "PARAMETER_BACKTEST_VERSION",
    "REQUIRED_HORIZONS",
    "FutureBar",
    "HistoricalParameterObservation",
    "HorizonMetrics",
    "ParameterBacktestResult",
    "ParameterScenario",
    "ParameterSensitivityService",
    "default_parameter_scenarios",
    "load_historical_parameter_observations",
]
