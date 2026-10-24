"""Wealth Lab 组合回测引擎（纯计算，无 IO，可独立测试）。

- 输入：后复权收盘矩阵（index=交易日, columns=symbol）
- 月度/季度再平衡：每个周期的首个交易日把权重重置为目标
- 交易成本：调仓日按 Σ|Δw| × cost_bps/1e4 从净值中扣减
  （等价于买入、卖出双边各按单边费率收取一次）
- 输出：净值/回撤序列、调仓事件、指标、基准对比
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from trading.wealth_lab.config import RF_ANNUAL, TRADING_DAYS_PER_YEAR

REBALANCE_MODES = ("monthly", "quarterly", "none")


@dataclass(frozen=True)
class BacktestParams:
    weights: dict[str, float]
    start_date: date
    end_date: date
    rebalance: str = "monthly"
    cost_bps: float = 5.0
    initial_capital: float = 1_000_000.0

    def validate(self) -> None:
        if not self.weights:
            raise ValueError("weights must not be empty")
        if any(w < 0 or w > 1 for w in self.weights.values()):
            raise ValueError("weights must be within [0, 1]")
        if abs(sum(self.weights.values()) - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")
        if self.rebalance not in REBALANCE_MODES:
            raise ValueError(f"rebalance must be one of {REBALANCE_MODES}")
        if self.cost_bps < 0 or self.cost_bps > 500:
            raise ValueError("cost_bps must be within [0, 500]")
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")


@dataclass
class RebalanceEvent:
    date: date
    trades: list[dict]
    cost: float


@dataclass
class BacktestResult:
    dates: list[date]
    nav: np.ndarray
    drawdown: np.ndarray
    daily_returns: np.ndarray
    rebalances: list[RebalanceEvent] = field(default_factory=list)


def _rebalance_mask(index: pd.DatetimeIndex, mode: str) -> np.ndarray:
    """每个再平衡周期的首个交易日为 True；none 仅期初建仓一次。"""
    mask = np.zeros(len(index), dtype=bool)
    if len(mask):
        mask[0] = True
    if mode == "none":
        return mask
    period = index.to_period("M" if mode == "monthly" else "Q")
    changed = np.concatenate([[True], np.asarray(period[1:] != period[:-1])])
    return mask | changed


def run_backtest(close: pd.DataFrame, params: BacktestParams) -> BacktestResult:
    """逐日漂移 + 周期再平衡 + 成本扣减。close 为后复权价矩阵。"""
    params.validate()
    symbols = [s for s in params.weights if s in close.columns]
    missing = set(params.weights) - set(symbols)
    if missing:
        raise ValueError(f"missing price series for: {sorted(missing)}")
    frame = close[symbols].dropna().loc[pd.Timestamp(params.start_date): pd.Timestamp(params.end_date)]
    if len(frame) < 2:
        raise ValueError(f"insufficient common trading days: {len(frame)}")

    returns = frame.pct_change().fillna(0.0)
    target = np.array([params.weights[s] for s in symbols], dtype=float)
    cost_ratio_unit = params.cost_bps / 1e4
    rebalance_flags = _rebalance_mask(frame.index, params.rebalance)

    n_days, n_assets = len(frame), len(symbols)
    nav = np.ones(n_days)
    daily = np.zeros(n_days)
    rebalances: list[RebalanceEvent] = []
    weights = np.zeros(n_assets)

    for i in range(n_days):
        day_ret = returns.iloc[i].to_numpy(dtype=float)
        if i == 0:
            # 期初从现金建仓
            portfolio_ret = 0.0
            gross_nav = 1.0
        else:
            portfolio_ret = float(weights @ day_ret)
            denom = 1.0 + portfolio_ret
            # 漂移到新占比：sum(weights) 恒为 1（权重即组合内市值占比）
            weights = weights * (1.0 + day_ret) / (denom if denom != 0 else 1.0)
            gross_nav = nav[i - 1] * (1.0 + portfolio_ret)

        if rebalance_flags[i]:
            delta = np.abs(target - weights)
            cost_ratio = float(delta.sum()) * cost_ratio_unit
            trades = [
                {
                    "symbol": symbols[j],
                    "weight_before": float(weights[j]),
                    "weight_after": float(target[j]),
                    "turnover": float(delta[j]),
                }
                for j in range(n_assets)
                if delta[j] > 1e-9
            ]
            cost_value = cost_ratio * params.initial_capital * (nav[i - 1] if i > 0 else 1.0)
            if trades or i == 0:
                rebalances.append(
                    RebalanceEvent(date=frame.index[i].date(), trades=trades, cost=float(cost_value))
                )
            weights = target.copy()
            gross_nav *= 1.0 - cost_ratio
            portfolio_ret = (1.0 + portfolio_ret) * (1.0 - cost_ratio) - 1.0

        nav[i] = gross_nav
        daily[i] = portfolio_ret

    running_max = np.maximum.accumulate(nav)
    drawdown = nav / running_max - 1.0
    return BacktestResult(
        dates=[idx.date() for idx in frame.index],
        nav=nav,
        drawdown=drawdown,
        daily_returns=daily,
        rebalances=rebalances,
    )


def compute_metrics(result: BacktestResult) -> dict:
    nav = result.nav
    daily = result.daily_returns
    n = len(nav)
    total_return = float(nav[-1] - 1.0)
    years = n / TRADING_DAYS_PER_YEAR
    annual_return = float(nav[-1] ** (1.0 / years) - 1.0) if years > 0 and nav[-1] > 0 else 0.0
    annual_volatility = float(np.std(daily, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if n > 2 else 0.0
    sharpe = (annual_return - RF_ANNUAL) / annual_volatility if annual_volatility > 0 else 0.0
    max_drawdown = float(result.drawdown.min()) if n else 0.0
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
    month_index = pd.to_datetime(result.dates).to_period("M")
    monthly_nav = pd.Series(nav, index=month_index).groupby(level=0).last()
    monthly_ret = monthly_nav.pct_change().dropna()
    win_rate = float((monthly_ret > 0).mean()) if len(monthly_ret) else 0.0
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe_ratio": float(sharpe),
        "max_drawdown": max_drawdown,
        "calmar_ratio": float(calmar),
        "monthly_win_rate": win_rate,
        "trading_days": n,
    }


def compute_rolling(result: BacktestResult) -> dict:
    daily = pd.Series(result.daily_returns)
    vol = daily.rolling(63).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    ann = (1 + daily).rolling(252).apply(
        lambda x: float(np.prod(x)) ** (TRADING_DAYS_PER_YEAR / len(x)) - 1.0, raw=True
    )
    sharpe = ((ann - RF_ANNUAL) / vol.replace(0, np.nan)).fillna(0.0)
    var_95 = float(np.percentile(daily.iloc[1:], 5)) if len(daily) > 20 else 0.0
    worst_idx = np.argsort(result.daily_returns)[:5]
    valid_idx = np.flatnonzero(vol.notna().to_numpy())
    return {
        "dates": [result.dates[i] for i in valid_idx],
        "volatility_63d": [float(vol.iloc[i]) for i in valid_idx],
        "sharpe_252d": [float(sharpe.iloc[i]) for i in valid_idx],
        "drawdown": [float(result.drawdown[i]) for i in valid_idx],
        "value_at_risk_95_1d": var_95,
        "worst_days": sorted(
            [
                {"date": result.dates[i], "daily_return": float(result.daily_returns[i])}
                for i in worst_idx
            ],
            key=lambda item: item["daily_return"],
        ),
    }


def generate_demo_close(symbols: list[str], days: int = 1600, seed: int = 20261024) -> pd.DataFrame:
    """DEMO 合成行情：固定 seed 的几何随机游走，仅供前端联调。

    刻意与真实历史无关；响应必须标注 data_mode=DEMO 防止混入真实结论。
    """
    rng = np.random.default_rng(seed)
    profiles = {"510300.SH": (0.06, 0.20), "511010.SH": (0.03, 0.015),
                "518880.SH": (0.06, 0.13), "511380.SH": (0.05, 0.06)}
    columns = {}
    idx = pd.bdate_range("2020-04-08", periods=days)
    for symbol in symbols:
        drift, vol = profiles.get(symbol, (0.04, 0.10))
        steps = rng.normal(drift / 252, vol / np.sqrt(252), size=days)
        columns[symbol] = 1_000 * np.cumprod(1 + steps)
    return pd.DataFrame(columns, index=idx)


# ===================== v2 策略回测（份额模型，支持现金与动态权重） =====================

@dataclass
class StrategyBacktestOutput:
    dates: list[date]
    nav: np.ndarray
    drawdown: np.ndarray
    daily_returns: np.ndarray
    cash_weight: np.ndarray
    weight_history: list[dict]
    rebalances: list[RebalanceEvent]


def run_strategy_backtest(
    close: pd.DataFrame,
    strategy,
    *,
    start_date: date,
    end_date: date,
    cost_bps: float = 5.0,
    initial_capital: float = 1_000_000.0,
) -> StrategyBacktestOutput:
    """统一策略回测：调仓日以截至前一交易日的数据决策（防未来函数）。

    份额模型：units_i 为资产 i 的份额（以首日价格归一），现金为 C。
    成本：Σ|Δ市值|/总资产 × cost_bps/1e4。
    """
    frame = close.loc[pd.Timestamp(start_date): pd.Timestamp(end_date)]
    if len(frame) < 2:
        raise ValueError(f"insufficient trading days: {len(frame)}")
    symbols = list(frame.columns)
    prices = frame.to_numpy(dtype=float)
    n_days, n_assets = prices.shape
    cost_unit = cost_bps / 1e4

    units = np.zeros(n_assets)
    cash = initial_capital
    target_w = np.zeros(n_assets)
    nav = np.zeros(n_days)
    daily = np.zeros(n_days)
    cash_weights = np.zeros(n_days)
    weight_history: list[dict] = []
    rebalances: list[RebalanceEvent] = []
    rebalance_flags = _rebalance_mask(frame.index, strategy.meta.rebalance)

    prev_total = initial_capital
    for i in range(n_days):
        decision_idx = i - 1
        if rebalance_flags[i]:
            history = frame.iloc[:i]
            try:
                target = strategy.target_weights(history)
            except Exception as exc:
                raise ValueError(f"strategy failed on {frame.index[i].date()}") from exc
            new_w = np.array([float(target.get(s, 0.0)) for s in symbols])
            if not np.isfinite(new_w).all() or (new_w < 0).any() or new_w.sum() > 1 + 1e-9:
                raise ValueError("invalid strategy weights")
            total_now = units @ prices[i] + cash
            delta_value = np.abs(new_w * total_now - units * prices[i])
            cost_ratio = float(delta_value.sum()) / max(total_now, 1e-12) * cost_unit
            cost_value = float(delta_value.sum()) * cost_unit
            trades = [
                {"symbol": symbols[j],
                 "weight_before": float(units[j] * prices[i][j] / max(total_now, 1e-12)),
                 "weight_after": float(new_w[j]),
                 "turnover": float(abs(new_w[j] - units[j] * prices[i][j] / max(total_now, 1e-12)))}
                for j in range(n_assets)
                if abs(new_w[j] - units[j] * prices[i][j] / max(total_now, 1e-12)) > 1e-9
            ]
            rebalances.append(RebalanceEvent(
                date=frame.index[i].date(), trades=trades, cost=round(cost_value, 2)))
            weight_history.append({
                "date": frame.index[i].date().isoformat(),
                "decision_data_through": (frame.index[decision_idx] if i else frame.index[0] - pd.Timedelta(days=1)).date().isoformat(),
                "weights": {s: round(float(new_w[j]), 6) for j, s in enumerate(symbols) if new_w[j] > 0},
            })
            units = new_w * total_now * (1.0 - cost_ratio) / np.maximum(prices[i], 1e-12)
            cash = total_now * (1.0 - float(new_w.sum())) * (1.0 - cost_ratio)
            target_w = new_w
        # 当日漂移
        if i > 0:
            gross = units @ prices[i] + cash
        else:
            gross = units @ prices[i] + cash
        nav[i] = gross
        daily[i] = gross / prev_total - 1.0 if prev_total > 0 else 0.0
        cash_weights[i] = cash / gross if gross > 0 else 0.0
        prev_total = gross

    nav = nav / initial_capital
    running_max = np.maximum.accumulate(np.r_[1.0, nav])[1:]
    drawdown = nav / running_max - 1.0
    return StrategyBacktestOutput(
        dates=[idx.date() for idx in frame.index], nav=nav, drawdown=drawdown,
        daily_returns=daily, cash_weight=cash_weights,
        weight_history=weight_history, rebalances=rebalances,
    )


def compute_v2_metrics(output: StrategyBacktestOutput) -> dict:
    base = compute_metrics(BacktestResult(
        dates=output.dates, nav=output.nav, drawdown=output.drawdown,
        daily_returns=output.daily_returns,
    ))
    daily = output.daily_returns
    downside = daily[daily < 0]
    downside_vol = float(np.std(downside, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(downside) > 2 else 0.0
    sortino = (
        (base["annual_return"] - RF_ANNUAL) / downside_vol if downside_vol > 0 else 0.0
    )
    turnovers = [
        t["turnover"] for rb in output.rebalances for t in rb.trades
    ] if output.rebalances else []
    avg_turnover = float(np.mean([sum(t["turnover"] for t in rb.trades) for rb in output.rebalances])) if output.rebalances else 0.0
    return {
        **base,
        "sortino_ratio": round(float(sortino), 4),
        "avg_turnover_per_rebalance": round(avg_turnover, 4),
    }
