from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import fmean, median

from trading.experiments.parameter_sensitivity import (
    REQUIRED_HORIZONS,
    FutureBar,
    HistoricalParameterObservation,
    ParameterScenario,
    ParameterSensitivityService,
    _portfolio_drawdown,
    _profit_loss_ratio,
)


@dataclass(frozen=True)
class AShareTradingConstraints:
    buy_commission_rate: float = 0.0003
    sell_commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    slippage_bps: float = 5.0
    price_limit_tolerance: float = 0.001


@dataclass(frozen=True)
class ConstrainedTrade:
    executed: bool
    net_return: float | None
    exposure: float
    benchmark_return: float | None
    exit_date: date | None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ValidationMetrics:
    horizon: int
    sample_count: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None
    profit_loss_ratio: float | None
    portfolio_maximum_drawdown: float | None
    csi300_excess_return: float | None
    blocked_entry_count: int = 0
    blocked_exit_count: int = 0


@dataclass(frozen=True)
class ConstraintComparison:
    scenario_id: str
    before: dict[int, ValidationMetrics]
    after: dict[int, ValidationMetrics]
    average_return_difference: dict[int, float | None]
    sample_count_difference: dict[int, int]
    technical_proxy_only: bool
    production_config_updated: bool = False
    orders_created: int = 0
    positions_changed: int = 0


def _limit_rate(symbol: str) -> float:
    code = symbol.split(".", 1)[0]
    if symbol.endswith(".BJ"):
        return 0.30
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def _one_price(bar: FutureBar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close)
    return all(value is not None for value in values) and (
        max(float(value) for value in values if value is not None)
        - min(float(value) for value in values if value is not None)
        <= 1e-9
    )


def _at_limit(
    price: float,
    previous_close: float | None,
    *,
    rate: float,
    direction: int,
    tolerance: float,
) -> bool:
    if previous_close is None or previous_close <= 0:
        return False
    limit_price = previous_close * (1 + direction * rate)
    return (
        price >= limit_price * (1 - tolerance)
        if direction > 0
        else price <= limit_price * (1 + tolerance)
    )


class AShareConstraintValidationService:
    """Research-only A-share execution simulation; never writes trade state."""

    @staticmethod
    def _constrained_trade(
        observation: HistoricalParameterObservation,
        scenario: ParameterScenario,
        horizon: int,
        base: tuple[float, float],
        constraints: AShareTradingConstraints,
    ) -> ConstrainedTrade:
        exposure = base[1]
        entry_bar = observation.future_bars[0]
        if (
            entry_bar.suspended
            or entry_bar.open is None
            or entry_bar.low is None
            or entry_bar.close is None
        ):
            return ConstrainedTrade(
                False, None, exposure, None, None, "SUSPENDED_OR_MISSING_ENTRY"
            )
        rate = _limit_rate(observation.symbol)
        if _one_price(entry_bar):
            return ConstrainedTrade(
                False, None, exposure, None, None, "ONE_PRICE_ENTRY"
            )
        if _at_limit(
            float(entry_bar.open),
            entry_bar.previous_close,
            rate=rate,
            direction=1,
            tolerance=constraints.price_limit_tolerance,
        ):
            return ConstrainedTrade(
                False, None, exposure, None, None, "LIMIT_UP_ENTRY"
            )

        stop = observation.signal_close - (
            scenario.atr_stop_multiplier * observation.atr14
        )
        # Bought on the next trading day. T+1 forbids selling that same day.
        scheduled_index = max(horizon - 1, 1)
        desired_exit_price: float | None = None
        desired_exit_index: int | None = None
        for index in range(1, min(scheduled_index + 1, len(observation.future_bars))):
            bar = observation.future_bars[index]
            if (
                bar.suspended
                or bar.open is None
                or bar.low is None
                or bar.close is None
            ):
                continue
            if stop > 0 and bar.low <= stop:
                desired_exit_price = min(float(bar.open), stop)
                desired_exit_index = index
                break
        if desired_exit_index is None:
            desired_exit_index = scheduled_index

        exit_price: float | None = None
        exit_date: date | None = None
        blocked_reason: str | None = None
        for index in range(desired_exit_index, len(observation.future_bars)):
            bar = observation.future_bars[index]
            if (
                bar.suspended
                or bar.open is None
                or bar.high is None
                or bar.low is None
                or bar.close is None
            ):
                blocked_reason = "SUSPENDED_OR_MISSING_EXIT"
                continue
            if _one_price(bar):
                blocked_reason = "ONE_PRICE_EXIT"
                continue
            if _at_limit(
                float(bar.close),
                bar.previous_close,
                rate=rate,
                direction=-1,
                tolerance=constraints.price_limit_tolerance,
            ):
                blocked_reason = "LIMIT_DOWN_EXIT"
                continue
            exit_price = (
                desired_exit_price
                if index == desired_exit_index and desired_exit_price is not None
                else float(bar.close)
            )
            exit_date = bar.trade_date
            break
        if exit_price is None or exit_date is None:
            return ConstrainedTrade(
                False, None, exposure, None, None, blocked_reason or "NO_EXECUTABLE_EXIT"
            )

        slippage = constraints.slippage_bps / 10_000
        buy_price = float(entry_bar.open) * (1 + slippage)
        sell_price = exit_price * (1 - slippage)
        net_return = (
            sell_price
            * (1 - constraints.sell_commission_rate - constraints.stamp_duty_rate)
            / (buy_price * (1 + constraints.buy_commission_rate))
            - 1
        )
        benchmark = observation.csi300_returns_by_exit_date.get(exit_date)
        return ConstrainedTrade(
            True,
            net_return,
            exposure,
            benchmark,
            exit_date,
        )

    @staticmethod
    def _metrics(
        horizon: int,
        ordered: list[tuple[date, str, float, float, float | None]],
        *,
        blocked_entry_count: int = 0,
        blocked_exit_count: int = 0,
        cohort_span: int | None = None,
    ) -> ValidationMetrics:
        ordered.sort(key=lambda item: (item[0], item[1]))
        returns = [item[2] for item in ordered]
        excess = [
            item[2] - item[4]
            for item in ordered
            if item[4] is not None
        ]
        return ValidationMetrics(
            horizon=horizon,
            sample_count=len(returns),
            win_rate=(
                None
                if not returns
                else sum(value > 0 for value in returns) / len(returns)
            ),
            average_return=None if not returns else fmean(returns),
            median_return=None if not returns else median(returns),
            profit_loss_ratio=_profit_loss_ratio(returns),
            portfolio_maximum_drawdown=_portfolio_drawdown(
                ordered, cohort_span or horizon
            ),
            csi300_excess_return=None if not excess else fmean(excess),
            blocked_entry_count=blocked_entry_count,
            blocked_exit_count=blocked_exit_count,
        )

    def compare(
        self,
        observations: list[HistoricalParameterObservation],
        scenario: ParameterScenario,
        constraints: AShareTradingConstraints | None = None,
    ) -> ConstraintComparison:
        policy = constraints or AShareTradingConstraints()
        before: dict[int, ValidationMetrics] = {}
        after: dict[int, ValidationMetrics] = {}
        for horizon in REQUIRED_HORIZONS:
            base_rows: list[tuple[date, str, float, float, float | None]] = []
            constrained_rows: list[
                tuple[date, str, float, float, float | None]
            ] = []
            blocked_entry = 0
            blocked_exit = 0
            for observation in observations:
                base = ParameterSensitivityService._return_for(
                    observation, scenario, horizon
                )
                if base is None:
                    continue
                base_rows.append(
                    (
                        observation.signal_date,
                        observation.symbol,
                        base[0],
                        base[1],
                        observation.csi300_returns.get(horizon),
                    )
                )
                constrained = self._constrained_trade(
                    observation,
                    scenario,
                    horizon,
                    base,
                    policy,
                )
                if constrained.executed and constrained.net_return is not None:
                    constrained_rows.append(
                        (
                            observation.signal_date,
                            observation.symbol,
                            constrained.net_return,
                            constrained.exposure,
                            constrained.benchmark_return,
                        )
                    )
                elif constrained.blocked_reason and "ENTRY" in constrained.blocked_reason:
                    blocked_entry += 1
                else:
                    blocked_exit += 1
            before[horizon] = self._metrics(horizon, base_rows)
            after[horizon] = self._metrics(
                horizon,
                constrained_rows,
                blocked_entry_count=blocked_entry,
                blocked_exit_count=blocked_exit,
                cohort_span=max(horizon, 2),
            )
        differences = {
            horizon: (
                None
                if before[horizon].average_return is None
                or after[horizon].average_return is None
                else after[horizon].average_return
                - before[horizon].average_return
            )
            for horizon in REQUIRED_HORIZONS
        }
        return ConstraintComparison(
            scenario_id=scenario.scenario_id,
            before=before,
            after=after,
            average_return_difference=differences,
            sample_count_difference={
                horizon: after[horizon].sample_count - before[horizon].sample_count
                for horizon in REQUIRED_HORIZONS
            },
            technical_proxy_only=any(
                item.score_source != "FORMAL_60_40"
                for item in observations
            ),
        )

    def compare_by_regime(
        self,
        observations: list[HistoricalParameterObservation],
        scenario: ParameterScenario,
        constraints: AShareTradingConstraints | None = None,
    ) -> dict[str, ConstraintComparison]:
        grouped: dict[str, list[HistoricalParameterObservation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.market_regime].append(observation)
        return {
            regime: self.compare(items, scenario, constraints)
            for regime, items in sorted(grouped.items())
        }


__all__ = [
    "AShareConstraintValidationService",
    "AShareTradingConstraints",
    "ConstrainedTrade",
    "ConstraintComparison",
    "ValidationMetrics",
]
