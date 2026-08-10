from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from trading.experiments.a_share_validation import AShareTradingConstraints


@dataclass(frozen=True)
class PortfolioTrade:
    packet_id: str
    symbol: str
    entry_date: date
    exit_date: date
    requested_weight: float
    adjusted_close_by_date: dict[date, float]


@dataclass(frozen=True)
class PortfolioCurveResult:
    daily_nav: dict[date, float]
    maximum_drawdown: float | None
    filled_trades: int
    cash_limited_trades: int
    maximum_simultaneous_positions: int
    maximum_gross_exposure: float
    minimum_cash_ratio: float


def simulate_portfolio_curve(
    trades: Iterable[PortfolioTrade],
    market_dates: Iterable[date],
    constraints: AShareTradingConstraints | None = None,
) -> PortfolioCurveResult:
    policy = constraints or AShareTradingConstraints()
    items = list(trades)
    entries: dict[date, list[PortfolioTrade]] = {}
    for trade in items:
        if trade.exit_date <= trade.entry_date:
            raise ValueError("portfolio trade must satisfy T+1")
        entries.setdefault(trade.entry_date, []).append(trade)
    cash = 1.0
    active: dict[str, tuple[PortfolioTrade, float, float]] = {}
    daily_nav: dict[date, float] = {}
    cash_limited = 0
    max_positions = 0
    max_gross = 0.0
    min_cash_ratio = 1.0
    slippage = policy.slippage_bps / 10_000

    def mark_value(day: date) -> float:
        total = 0.0
        for trade, units, last_mark in active.values():
            mark = trade.adjusted_close_by_date.get(day, last_mark)
            total += units * mark
        return total

    for day in sorted(set(market_dates)):
        for packet_id, (trade, units, last_mark) in list(active.items()):
            mark = trade.adjusted_close_by_date.get(day, last_mark)
            if day >= trade.exit_date:
                cash += (
                    units * mark * (1 - slippage)
                    * (1 - policy.sell_commission_rate - policy.stamp_duty_rate)
                )
                del active[packet_id]
            else:
                active[packet_id] = (trade, units, mark)

        nav_before = cash + mark_value(day)
        requests = []
        for trade in sorted(entries.get(day, []), key=lambda item: item.packet_id):
            mark = trade.adjusted_close_by_date.get(day)
            if mark is None or mark <= 0 or trade.requested_weight <= 0:
                continue
            requests.append((trade, mark, trade.requested_weight * nav_before))
        requested_total = sum(item[2] for item in requests)
        scale = (
            1.0 if requested_total <= cash or requested_total <= 0
            else cash / requested_total
        )
        for trade, mark, requested in requests:
            allocation = requested * scale
            if allocation + 1e-12 < requested:
                cash_limited += 1
            units = allocation / (
                mark * (1 + slippage) * (1 + policy.buy_commission_rate)
            )
            cash -= allocation
            active[trade.packet_id] = (trade, units, mark)

        holdings = mark_value(day)
        nav = cash + holdings
        daily_nav[day] = nav
        max_positions = max(max_positions, len(active))
        gross = 0.0 if nav <= 0 else holdings / nav
        max_gross = max(max_gross, gross)
        min_cash_ratio = min(min_cash_ratio, 0.0 if nav <= 0 else cash / nav)

    peak = 0.0
    maximum_drawdown = 0.0
    for nav in daily_nav.values():
        peak = max(peak, nav)
        if peak > 0:
            maximum_drawdown = min(maximum_drawdown, nav / peak - 1)
    return PortfolioCurveResult(
        daily_nav=daily_nav,
        maximum_drawdown=None if not daily_nav else maximum_drawdown,
        filled_trades=len(items),
        cash_limited_trades=cash_limited,
        maximum_simultaneous_positions=max_positions,
        maximum_gross_exposure=max_gross,
        minimum_cash_ratio=min_cash_ratio,
    )


__all__ = ["PortfolioCurveResult", "PortfolioTrade", "simulate_portfolio_curve"]
