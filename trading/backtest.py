from __future__ import annotations

import math
from statistics import fmean, pstdev

from trading.schemas import BacktestRequest, BacktestResult, BacktestTrade, OrderSide


def run_backtest(request: BacktestRequest) -> BacktestResult:
    """Long-only SMA strategy with next-open fills and protective exits."""
    bars = sorted(request.bars, key=lambda bar: bar.trade_date)
    cash = request.initial_cash
    quantity = 0
    entry_price = 0.0
    equity_curve: list[tuple[object, float]] = []
    trades: list[BacktestTrade] = []
    slip = request.slippage_bps / 10_000

    for index, bar in enumerate(bars):
        if index >= 60:
            previous = bars[index - 1]
            history = [item.close for item in bars[index - 60 : index]]
            sma20 = fmean(history[-20:])
            sma60 = fmean(history)
            exit_reason: str | None = None
            exit_price = bar.open * (1 - slip)
            if quantity and bar.low <= entry_price * 0.93:
                exit_reason = "7% stop loss"
                exit_price = min(bar.open, entry_price * 0.93) * (1 - slip)
            elif quantity and bar.high >= entry_price * 1.15:
                exit_reason = "15% take profit"
                exit_price = max(bar.open, entry_price * 1.15) * (1 - slip)
            elif quantity and sma20 <= sma60:
                exit_reason = "previous-close SMA20 crossed below SMA60"
            if exit_reason:
                gross = quantity * exit_price
                fee = gross * request.commission_rate
                cash += gross - fee
                trades.append(BacktestTrade(
                    trade_date=bar.trade_date,
                    side=OrderSide.SELL,
                    quantity=quantity,
                    price=exit_price,
                    fee=fee,
                    reason=exit_reason,
                ))
                quantity = 0
                entry_price = 0
            elif not quantity and sma20 > sma60 and previous.volume > 0:
                buy_price = bar.open * (1 + slip)
                budget = min(cash, request.initial_cash * request.max_position_weight)
                buy_quantity = int(budget / (buy_price * 100)) * 100
                gross = buy_quantity * buy_price
                fee = gross * request.commission_rate
                if buy_quantity > 0 and gross + fee <= cash:
                    cash -= gross + fee
                    quantity = buy_quantity
                    entry_price = buy_price
                    trades.append(BacktestTrade(
                        trade_date=bar.trade_date,
                        side=OrderSide.BUY,
                        quantity=quantity,
                        price=buy_price,
                        fee=fee,
                        reason="previous-close SMA20 above SMA60",
                    ))
        equity_curve.append((bar.trade_date, cash + quantity * bar.close))

    equities = [value for _, value in equity_curve]
    returns = [b / a - 1 for a, b in zip(equities[:-1], equities[1:]) if a]
    peak = equities[0]
    max_drawdown = 0.0
    for value in equities:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    volatility = pstdev(returns) if len(returns) > 1 else 0
    sharpe = (fmean(returns) / volatility * math.sqrt(252)) if volatility else 0.0
    final_equity = equities[-1]
    return BacktestResult(
        symbol=request.symbol,
        initial_cash=request.initial_cash,
        final_equity=final_equity,
        total_return=final_equity / request.initial_cash - 1,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        trades=trades,
        equity_curve=equity_curve,
        assumptions=[
            "Signals use only data available at the previous close; fills occur at next open.",
            "Commission and configurable slippage are charged on every fill.",
            "Corporate actions, limit-up/down, suspension, tax, and liquidity are not yet modeled.",
        ],
    )
