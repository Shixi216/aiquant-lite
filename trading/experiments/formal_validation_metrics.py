from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from statistics import fmean, median
from typing import Iterable

from trading.experiments.a_share_validation import (
    AShareTradingConstraints,
    _at_limit,
    _limit_rate,
    _one_price,
)
from trading.experiments.formal_strategy_validation import FormalReplayDecision
from trading.experiments.parameter_sensitivity import (
    REQUIRED_HORIZONS,
    FutureBar,
    _portfolio_drawdown,
    _profit_loss_ratio,
)


BUY_ACTIONS = frozenset({"STRONG_BUY", "BUY", "SMALL_BUY"})


@dataclass(frozen=True)
class FormalValidationMetrics:
    horizon: int
    sample_count: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None
    profit_loss_ratio: float | None
    portfolio_maximum_drawdown: float | None
    csi300_excess_return: float | None
    industry_excess_return: float | None
    blocked_entry_count: int = 0
    blocked_exit_count: int = 0


@dataclass(frozen=True)
class FormalValidationComparison:
    decision_count: int
    actionable_count: int
    before_constraints: dict[int, FormalValidationMetrics]
    after_constraints: dict[int, FormalValidationMetrics]
    average_return_difference: dict[int, float | None]
    production_config_updated: bool = False
    orders_created: int = 0
    positions_changed: int = 0


@dataclass(frozen=True)
class _Trade:
    executed: bool
    net_return: float | None
    exposure: float
    exit_date: date | None
    blocked_reason: str | None = None


def _factor(bar: FutureBar) -> float:
    value = bar.adjustment_factor
    return 1.0 if value is None or value <= 0 else float(value)


def _adjusted_return(
    *,
    entry_price: float,
    entry_bar: FutureBar,
    exit_price: float,
    exit_bar: FutureBar,
) -> float:
    return (
        exit_price * _factor(exit_bar)
        / (entry_price * _factor(entry_bar))
        - 1
    )


def _preferred_entry(decision: FormalReplayDecision, bar: FutureBar) -> bool:
    zone = decision.frozen_preferred_zone or decision.frozen_entry_zone
    return (
        len(zone) == 2
        and bar.open is not None
        and float(zone[0]) <= float(bar.open) <= float(zone[1])
    )


def _exposure(decision: FormalReplayDecision) -> float:
    if decision.recommended_batches <= 0:
        return 0.0
    return decision.target_position_ratio / decision.recommended_batches


class FormalValidationMetricsService:
    """Evaluate immutable DecisionEngine outputs; no persistence boundary."""

    @staticmethod
    def _unconstrained_trade(
        decision: FormalReplayDecision,
        horizon: int,
    ) -> _Trade:
        bars = decision.observation.future_bars
        exposure = _exposure(decision)
        if (
            decision.action not in BUY_ACTIONS
            or not decision.point_in_time_valid
            or exposure <= 0
            or len(bars) < horizon
            or not _preferred_entry(decision, bars[0])
        ):
            return _Trade(False, None, exposure, None, "NOT_ACTIONABLE_ENTRY")
        entry = float(bars[0].open)
        exit_index = horizon - 1
        exit_price = bars[exit_index].close
        if exit_price is None:
            return _Trade(False, None, exposure, None, "MISSING_EXIT")
        stop = decision.frozen_stop_loss_price
        for index, bar in enumerate(bars[:horizon]):
            if (
                stop is not None and stop > 0
                and bar.open is not None and bar.low is not None
                and float(bar.low) <= stop
            ):
                exit_index = index
                exit_price = min(float(bar.open), float(stop))
                break
        return _Trade(
            True,
            _adjusted_return(
                entry_price=entry,
                entry_bar=bars[0],
                exit_price=float(exit_price),
                exit_bar=bars[exit_index],
            ),
            exposure,
            bars[exit_index].trade_date,
        )

    @staticmethod
    def _constrained_trade(
        decision: FormalReplayDecision,
        horizon: int,
        constraints: AShareTradingConstraints,
    ) -> _Trade:
        bars = decision.observation.future_bars
        exposure = _exposure(decision)
        if (
            decision.action not in BUY_ACTIONS
            or not decision.point_in_time_valid
            or exposure <= 0
            or len(bars) < horizon
        ):
            return _Trade(False, None, exposure, None, "NOT_ACTIONABLE_ENTRY")
        entry_bar = bars[0]
        if (
            entry_bar.suspended or entry_bar.open is None
            or entry_bar.high is None or entry_bar.low is None
            or entry_bar.close is None
        ):
            return _Trade(False, None, exposure, None, "SUSPENDED_OR_MISSING_ENTRY")
        if not _preferred_entry(decision, entry_bar):
            return _Trade(False, None, exposure, None, "OUTSIDE_FROZEN_PREFERRED_ZONE")
        if _one_price(entry_bar):
            return _Trade(False, None, exposure, None, "ONE_PRICE_ENTRY")
        limit_rate = _limit_rate(decision.symbol)
        if _at_limit(
            float(entry_bar.open), entry_bar.previous_close,
            rate=limit_rate, direction=1,
            tolerance=constraints.price_limit_tolerance,
        ):
            return _Trade(False, None, exposure, None, "LIMIT_UP_ENTRY")

        stop = decision.frozen_stop_loss_price
        scheduled_index = max(horizon - 1, 1)
        desired_index = scheduled_index
        desired_price: float | None = None
        # The position is bought on future_bars[0]; T+1 starts exit checks at 1.
        for index in range(1, min(scheduled_index + 1, len(bars))):
            bar = bars[index]
            if (
                bar.suspended or bar.open is None
                or bar.low is None or bar.close is None
            ):
                continue
            if stop is not None and stop > 0 and float(bar.low) <= stop:
                desired_index = index
                desired_price = min(float(bar.open), float(stop))
                break

        blocked_reason: str | None = None
        exit_index: int | None = None
        exit_price: float | None = None
        for index in range(desired_index, len(bars)):
            bar = bars[index]
            if (
                bar.suspended or bar.open is None or bar.high is None
                or bar.low is None or bar.close is None
            ):
                blocked_reason = "SUSPENDED_OR_MISSING_EXIT"
                continue
            if _one_price(bar):
                blocked_reason = "ONE_PRICE_EXIT"
                continue
            if _at_limit(
                float(bar.close), bar.previous_close,
                rate=limit_rate, direction=-1,
                tolerance=constraints.price_limit_tolerance,
            ):
                blocked_reason = "LIMIT_DOWN_EXIT"
                continue
            exit_index = index
            exit_price = (
                desired_price
                if index == desired_index and desired_price is not None
                else float(bar.close)
            )
            break
        if exit_index is None or exit_price is None:
            return _Trade(
                False, None, exposure, None,
                blocked_reason or "NO_EXECUTABLE_EXIT",
            )

        slippage = constraints.slippage_bps / 10_000
        buy_price = float(entry_bar.open) * (1 + slippage)
        sell_price = exit_price * (1 - slippage)
        gross_adjusted = (
            sell_price * _factor(bars[exit_index])
            / (buy_price * _factor(entry_bar))
        )
        net_return = (
            gross_adjusted
            * (1 - constraints.sell_commission_rate - constraints.stamp_duty_rate)
            / (1 + constraints.buy_commission_rate)
            - 1
        )
        return _Trade(
            True, net_return, exposure, bars[exit_index].trade_date
        )

    @staticmethod
    def _metrics(
        horizon: int,
        rows: list[tuple[date, str, float, float, float | None, float | None]],
        *,
        blocked_entry_count: int = 0,
        blocked_exit_count: int = 0,
    ) -> FormalValidationMetrics:
        rows.sort(key=lambda item: (item[0], item[1]))
        returns = [item[2] for item in rows]
        csi_excess = [
            item[2] - item[4] for item in rows if item[4] is not None
        ]
        industry_excess = [
            item[2] - item[5] for item in rows if item[5] is not None
        ]
        return FormalValidationMetrics(
            horizon=horizon,
            sample_count=len(returns),
            win_rate=(None if not returns else sum(x > 0 for x in returns) / len(returns)),
            average_return=None if not returns else fmean(returns),
            median_return=None if not returns else median(returns),
            profit_loss_ratio=_profit_loss_ratio(returns),
            portfolio_maximum_drawdown=_portfolio_drawdown(
                [(a, b, c, d, e) for a, b, c, d, e, _ in rows],
                max(horizon, 2),
            ),
            csi300_excess_return=None if not csi_excess else fmean(csi_excess),
            industry_excess_return=(
                None if not industry_excess else fmean(industry_excess)
            ),
            blocked_entry_count=blocked_entry_count,
            blocked_exit_count=blocked_exit_count,
        )

    def compare(
        self,
        decisions: Iterable[FormalReplayDecision],
        constraints: AShareTradingConstraints | None = None,
    ) -> FormalValidationComparison:
        items = list(decisions)
        policy = constraints or AShareTradingConstraints()
        before: dict[int, FormalValidationMetrics] = {}
        after: dict[int, FormalValidationMetrics] = {}
        for horizon in REQUIRED_HORIZONS:
            before_rows = []
            after_rows = []
            blocked_entry = 0
            blocked_exit = 0
            for decision in items:
                plain = self._unconstrained_trade(decision, horizon)
                if plain.executed and plain.net_return is not None and plain.exit_date:
                    before_rows.append((
                        decision.signal_date, decision.symbol,
                        plain.net_return, plain.exposure,
                        decision.observation.csi300_returns_by_exit_date.get(plain.exit_date),
                        decision.industry_returns_by_exit_date.get(plain.exit_date),
                    ))
                constrained = self._constrained_trade(decision, horizon, policy)
                if constrained.executed and constrained.net_return is not None and constrained.exit_date:
                    after_rows.append((
                        decision.signal_date, decision.symbol,
                        constrained.net_return, constrained.exposure,
                        decision.observation.csi300_returns_by_exit_date.get(constrained.exit_date),
                        decision.industry_returns_by_exit_date.get(constrained.exit_date),
                    ))
                elif constrained.blocked_reason not in {"NOT_ACTIONABLE_ENTRY"}:
                    if constrained.blocked_reason and (
                        "ENTRY" in constrained.blocked_reason
                        or "FROZEN" in constrained.blocked_reason
                    ):
                        blocked_entry += 1
                    else:
                        blocked_exit += 1
            before[horizon] = self._metrics(horizon, before_rows)
            after[horizon] = self._metrics(
                horizon, after_rows,
                blocked_entry_count=blocked_entry,
                blocked_exit_count=blocked_exit,
            )
        return FormalValidationComparison(
            decision_count=len(items),
            actionable_count=sum(
                item.action in BUY_ACTIONS and item.point_in_time_valid
                for item in items
            ),
            before_constraints=before,
            after_constraints=after,
            average_return_difference={
                horizon: (
                    None
                    if before[horizon].average_return is None
                    or after[horizon].average_return is None
                    else after[horizon].average_return
                    - before[horizon].average_return
                )
                for horizon in REQUIRED_HORIZONS
            },
        )

    def candidate_forward_outcomes(
        self,
        decisions: Iterable[FormalReplayDecision],
    ) -> dict[int, FormalValidationMetrics]:
        """Layer diagnostic only; this is not a claim that a trade was placed."""
        items = list(decisions)
        output: dict[int, FormalValidationMetrics] = {}
        for horizon in REQUIRED_HORIZONS:
            rows = []
            for decision in items:
                bars = decision.observation.future_bars
                if len(bars) < horizon:
                    continue
                entry_bar = bars[0]
                exit_bar = bars[horizon - 1]
                if entry_bar.open is None or exit_bar.close is None:
                    continue
                value = _adjusted_return(
                    entry_price=float(entry_bar.open),
                    entry_bar=entry_bar,
                    exit_price=float(exit_bar.close),
                    exit_bar=exit_bar,
                )
                rows.append((
                    decision.signal_date, decision.symbol, value, 1.0,
                    decision.observation.csi300_returns.get(horizon),
                    decision.industry_returns_by_exit_date.get(exit_bar.trade_date),
                ))
            output[horizon] = self._metrics(horizon, rows)
        return output

    def candidate_outcomes_by_layer(
        self,
        decisions: Iterable[FormalReplayDecision],
    ) -> dict[str, dict[int, FormalValidationMetrics]]:
        grouped: dict[str, list[FormalReplayDecision]] = defaultdict(list)
        for item in decisions:
            grouped[item.layer.value].append(item)
        return {
            key: self.candidate_forward_outcomes(value)
            for key, value in sorted(grouped.items())
        }
    def compare_by_layer(
        self,
        decisions: Iterable[FormalReplayDecision],
    ) -> dict[str, FormalValidationComparison]:
        grouped: dict[str, list[FormalReplayDecision]] = defaultdict(list)
        for item in decisions:
            grouped[item.layer.value].append(item)
        return {key: self.compare(value) for key, value in sorted(grouped.items())}

    def compare_by_regime(
        self,
        decisions: Iterable[FormalReplayDecision],
    ) -> dict[str, FormalValidationComparison]:
        grouped: dict[str, list[FormalReplayDecision]] = defaultdict(list)
        for item in decisions:
            grouped[item.market_regime].append(item)
        return {key: self.compare(value) for key, value in sorted(grouped.items())}


def comparison_to_dict(value: FormalValidationComparison) -> dict:
    return asdict(value)


__all__ = [
    "BUY_ACTIONS",
    "FormalValidationComparison",
    "FormalValidationMetrics",
    "FormalValidationMetricsService",
    "comparison_to_dict",
]