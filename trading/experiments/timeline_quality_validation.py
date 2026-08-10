from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil
from statistics import fmean, median
from typing import Iterable

import pandas as pd

from trading.experiments.a_share_validation import (
    AShareTradingConstraints,
    _at_limit,
    _limit_rate,
    _one_price,
)
from trading.experiments.parameter_sensitivity import (
    REQUIRED_HORIZONS,
    FutureBar,
    _portfolio_drawdown,
    _profit_loss_ratio,
)


@dataclass(frozen=True)
class TimelineTradeSample:
    packet_id: str
    symbol: str
    signal_date: date
    entry_date: date
    entry_day: int
    market_regime: str
    core_structure_at_entry: bool | None
    bars: tuple[FutureBar, ...]
    csi300_close_by_date: dict[date, float]
    industry_close_by_date: dict[date, float]


@dataclass(frozen=True)
class TimelineTradeResult:
    packet_id: str
    symbol: str
    entry_date: date
    horizon: int
    executed: bool
    gross_return: float | None
    net_return: float | None
    exit_date: date | None
    benchmark_return: float | None
    industry_return: float | None
    blocked_reason: str | None = None
    exit_delay_days: int = 0


def _factor(bar: FutureBar) -> float:
    value = bar.adjustment_factor
    return 1.0 if value is None or value <= 0 else float(value)


def _relative_return(entry: FutureBar, exit_: FutureBar) -> float:
    if entry.close is None or exit_.close is None:
        raise ValueError("entry and exit close are required")
    return float(exit_.close) * _factor(exit_) / (
        float(entry.close) * _factor(entry)
    ) - 1


def _benchmark_return(
    values: dict[date, float], entry_date: date, exit_date: date,
) -> float | None:
    entry = values.get(entry_date)
    exit_ = values.get(exit_date)
    if entry is None or exit_ is None or entry <= 0:
        return None
    return exit_ / entry - 1


class TimelineConstraintValidationService:
    """Research-only constraints for already-selected preferred-zone samples."""

    @staticmethod
    def evaluate_unconstrained(
        sample: TimelineTradeSample,
        horizon: int,
    ) -> TimelineTradeResult:
        bars = list(sample.bars)
        entry_index = next(
            (i for i, item in enumerate(bars) if item.trade_date == sample.entry_date),
            None,
        )
        if entry_index is None or bars[entry_index].close is None:
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "MISSING_ENTRY",
            )
        exit_index = entry_index + horizon
        if exit_index >= len(bars) or bars[exit_index].close is None:
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "MISSING_HORIZON_EXIT",
            )
        entry = bars[entry_index]
        exit_bar = bars[exit_index]
        value = _relative_return(entry, exit_bar)
        return TimelineTradeResult(
            packet_id=sample.packet_id,
            symbol=sample.symbol,
            entry_date=sample.entry_date,
            horizon=horizon,
            executed=True,
            gross_return=value,
            net_return=value,
            exit_date=exit_bar.trade_date,
            benchmark_return=_benchmark_return(
                sample.csi300_close_by_date, sample.entry_date, exit_bar.trade_date
            ),
            industry_return=_benchmark_return(
                sample.industry_close_by_date, sample.entry_date, exit_bar.trade_date
            ),
        )

    @staticmethod
    def evaluate(
        sample: TimelineTradeSample,
        horizon: int,
        constraints: AShareTradingConstraints | None = None,
    ) -> TimelineTradeResult:
        policy = constraints or AShareTradingConstraints()
        bars = list(sample.bars)
        entry_index = next(
            (i for i, item in enumerate(bars) if item.trade_date == sample.entry_date),
            None,
        )
        if entry_index is None:
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "MISSING_ENTRY",
            )
        entry = bars[entry_index]
        if (
            entry.suspended or entry.close is None or entry.open is None
            or entry.high is None or entry.low is None
        ):
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None,
                "SUSPENDED_OR_MISSING_ENTRY",
            )
        if _one_price(entry):
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "ONE_PRICE_ENTRY",
            )
        if _at_limit(
            float(entry.close), entry.previous_close,
            rate=_limit_rate(sample.symbol), direction=1,
            tolerance=policy.price_limit_tolerance,
        ):
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "LIMIT_UP_ENTRY",
            )

        scheduled = entry_index + horizon
        if scheduled >= len(bars):
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, None, None, None, None, None, "MISSING_HORIZON_EXIT",
            )
        gross_exit = bars[scheduled]
        gross = (
            None if gross_exit.close is None
            else _relative_return(entry, gross_exit)
        )
        blocked_reason: str | None = None
        exit_index: int | None = None
        for index in range(scheduled, len(bars)):
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
                rate=_limit_rate(sample.symbol), direction=-1,
                tolerance=policy.price_limit_tolerance,
            ):
                blocked_reason = "LIMIT_DOWN_EXIT"
                continue
            exit_index = index
            break
        if exit_index is None:
            return TimelineTradeResult(
                sample.packet_id, sample.symbol, sample.entry_date, horizon,
                False, gross, None, None, None, None,
                blocked_reason or "NO_EXECUTABLE_EXIT",
            )

        exit_bar = bars[exit_index]
        slippage = policy.slippage_bps / 10_000
        price_ratio = (
            float(exit_bar.close) * (1 - slippage) * _factor(exit_bar)
            / (float(entry.close) * (1 + slippage) * _factor(entry))
        )
        net = (
            price_ratio
            * (1 - policy.sell_commission_rate - policy.stamp_duty_rate)
            / (1 + policy.buy_commission_rate)
            - 1
        )
        return TimelineTradeResult(
            packet_id=sample.packet_id,
            symbol=sample.symbol,
            entry_date=sample.entry_date,
            horizon=horizon,
            executed=True,
            gross_return=gross,
            net_return=net,
            exit_date=exit_bar.trade_date,
            benchmark_return=_benchmark_return(
                sample.csi300_close_by_date, sample.entry_date, exit_bar.trade_date
            ),
            industry_return=_benchmark_return(
                sample.industry_close_by_date, sample.entry_date, exit_bar.trade_date
            ),
            blocked_reason=blocked_reason,
            exit_delay_days=exit_index - scheduled,
        )

    def evaluate_many(
        self,
        samples: Iterable[TimelineTradeSample],
        constraints: AShareTradingConstraints | None = None,
    ) -> dict[int, list[TimelineTradeResult]]:
        items = list(samples)
        return {
            horizon: [self.evaluate(item, horizon, constraints) for item in items]
            for horizon in REQUIRED_HORIZONS
        }

    @staticmethod
    def evaluate_many_unconstrained(
        samples: Iterable[TimelineTradeSample],
    ) -> dict[int, list[TimelineTradeResult]]:
        items = list(samples)
        return {
            horizon: [
                TimelineConstraintValidationService.evaluate_unconstrained(
                    item, horizon
                )
                for item in items
            ]
            for horizon in REQUIRED_HORIZONS
        }

def summarize_results(
    results: Iterable[TimelineTradeResult],
    *,
    constrained: bool,
) -> dict[str, float | int | None | dict[str, int]]:
    items = list(results)
    rows: list[tuple[date, str, float, float, float | None]] = []
    industry_excess: list[float] = []
    blocked: dict[str, int] = {}
    delayed = 0
    for item in items:
        value = item.net_return if constrained else item.gross_return
        usable = item.executed if constrained else value is not None
        if usable and value is not None:
            rows.append((
                item.entry_date, item.symbol, float(value), 1.0,
                item.benchmark_return,
            ))
            if item.industry_return is not None:
                industry_excess.append(float(value) - item.industry_return)
            delayed += int(item.exit_delay_days > 0)
        elif constrained:
            reason = item.blocked_reason or "UNKNOWN"
            blocked[reason] = blocked.get(reason, 0) + 1
    rows.sort(key=lambda row: (row[0], row[1]))
    values = [row[2] for row in rows]
    benchmark_excess = [
        row[2] - row[4] for row in rows if row[4] is not None
    ]
    horizon = items[0].horizon if items else 1
    return {
        "sample_count": len(values),
        "win_rate": None if not values else sum(x > 0 for x in values) / len(values),
        "average_return": None if not values else fmean(values),
        "median_return": None if not values else median(values),
        "profit_loss_ratio": _profit_loss_ratio(values),
        "portfolio_maximum_drawdown": _portfolio_drawdown(
            rows, max(horizon, 2)
        ),
        "csi300_excess_return": (
            None if not benchmark_excess else fmean(benchmark_excess)
        ),
        "industry_excess_return": (
            None if not industry_excess else fmean(industry_excess)
        ),
        "blocked": dict(sorted(blocked.items())),
        "delayed_exit_count": delayed,
    }


def distribution_diagnostics(values: Iterable[float]) -> dict[str, object]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"sample_count": 0}
    series = pd.Series(ordered, dtype="float64")
    total = sum(ordered)
    output: dict[str, object] = {
        "sample_count": len(ordered),
        "quantiles": {
            key: float(series.quantile(level))
            for key, level in (
                ("p10", .10), ("p25", .25), ("p50", .50),
                ("p75", .75), ("p90", .90),
            )
        },
        "maximum_gain": ordered[-1],
        "maximum_loss": ordered[0],
    }
    for percent in (1, 5, 10):
        count = max(1, ceil(len(ordered) * percent / 100))
        top_sum = sum(ordered[-count:])
        remaining = ordered[:-count]
        output[f"top_{percent}_percent"] = {
            "count": count,
            "aggregate_return": top_sum,
            "share_of_aggregate_return": (
                None if abs(total) <= 1e-12 else top_sum / total
            ),
            "average_without_top": (
                None if not remaining else fmean(remaining)
            ),
        }
    return output


__all__ = [
    "TimelineConstraintValidationService",
    "TimelineTradeResult",
    "TimelineTradeSample",
    "distribution_diagnostics",
    "summarize_results",
]
