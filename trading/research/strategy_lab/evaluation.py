from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


PRIMARY_COST_BP = 40
ANNUAL_PERIODS = 252.0 / 20.0


@dataclass(frozen=True)
class StrategyMetric:
    candidate_id: str
    cost_bp: int
    observation_count: int
    valid_dates: int
    portfolio_periods: int
    trade_count: int
    annualized_return: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    calmar: float | None
    win_rate: float | None
    profit_factor: float | None
    turnover: float | None
    average_holding_period: float
    ic: float | None
    rank_ic: float | None
    ic_ir: float | None
    top_bottom_spread: float | None
    tail_loss: float | None
    monthly_stability: float | None
    yearly_stability: float | None
    mean_return: float | None


def _safe(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _drawdown(returns: np.ndarray) -> float | None:
    if not returns.size:
        return None
    wealth = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], wealth)))[:-1]
    return float(np.min(wealth / peaks - 1.0))


def _period_stats(returns: np.ndarray) -> dict[str, float | None]:
    if not returns.size:
        return {key: None for key in (
            "annualized_return", "sharpe", "sortino", "max_drawdown", "calmar",
            "win_rate", "profit_factor", "tail_loss", "mean_return",
        )}
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0
    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if downside.size > 1 else 0.0
    total = float(np.prod(1.0 + returns))
    annualized = total ** (ANNUAL_PERIODS / returns.size) - 1.0 if total > 0 else -1.0
    sharpe = mean / std * math.sqrt(ANNUAL_PERIODS) if std > 0 else None
    sortino = mean / downside_std * math.sqrt(ANNUAL_PERIODS) if downside_std > 0 else None
    max_drawdown = _drawdown(returns)
    calmar = annualized / abs(max_drawdown) if max_drawdown not in (None, 0.0) else None
    gains = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    return {
        "annualized_return": _safe(annualized),
        "sharpe": _safe(sharpe),
        "sortino": _safe(sortino),
        "max_drawdown": _safe(max_drawdown),
        "calmar": _safe(calmar),
        "win_rate": float(np.mean(returns > 0)),
        "profit_factor": _safe(gains / losses if losses > 0 else None),
        "tail_loss": _safe(np.quantile(returns, 0.05)),
        "mean_return": mean,
    }


def evaluate_candidate(
    frame: pd.DataFrame,
    *,
    candidate_id: str,
    score: pd.Series | np.ndarray,
    risk_scale: float = 1.0,
    cost_scenarios: tuple[int, ...] = (0, 20, 40, 60),
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    work = frame[["trade_date", "symbol", "return_20d", "risk_reminder"]].copy()
    work["score"] = np.asarray(score, dtype=float)
    work = work[np.isfinite(work.score) & np.isfinite(work.return_20d)].copy()
    if work.empty:
        return [], pd.DataFrame(), pd.DataFrame()
    work["score_rank"] = work.groupby("trade_date", observed=True).score.rank(
        pct=True, method="average"
    )
    work["return_rank"] = work.groupby("trade_date", observed=True).return_20d.rank(
        pct=True, method="average"
    )
    daily_rows: list[dict[str, Any]] = []
    portfolio_rows: list[dict[str, Any]] = []
    all_dates = sorted(work.trade_date.unique())
    rebalance_dates = set(all_dates[::20])
    for trade_date, group in work.groupby("trade_date", sort=True, observed=True):
        if len(group) < 100:
            continue
        ic = group.score.corr(group.return_20d)
        rank_ic = group.score_rank.corr(group.return_rank)
        low = group[group.score_rank <= 0.1].return_20d.mean()
        high = group[group.score_rank > 0.9].return_20d.mean()
        daily_rows.append(
            {
                "candidate_id": candidate_id,"trade_date": trade_date,"sample_count": len(group),
                "ic": ic,"rank_ic": rank_ic,"top_bottom_spread": high - low,
            }
        )
        if trade_date not in rebalance_dates:
            continue
        selected = group[group.score_rank > 0.9]
        if selected.empty:
            continue
        scales = np.where(selected.risk_reminder.to_numpy(dtype=bool), risk_scale, 1.0)
        gross_return = float(np.sum(selected.return_20d.to_numpy() * scales) / len(selected))
        exposure = float(np.sum(scales) / len(selected))
        portfolio_rows.append(
            {
                "candidate_id": candidate_id,"trade_date": trade_date,
                "gross_return": gross_return,"exposure": exposure,
                "selected_count": len(selected),"risk_selected_count": int(selected.risk_reminder.sum()),
            }
        )
    daily = pd.DataFrame(daily_rows)
    portfolios = pd.DataFrame(portfolio_rows)
    if daily.empty or portfolios.empty:
        return [], daily, portfolios
    rank_ic_mean = float(daily.rank_ic.mean())
    rank_ic_std = float(daily.rank_ic.std(ddof=1))
    ic_ir = rank_ic_mean / rank_ic_std * math.sqrt(252.0) if rank_ic_std > 0 else None
    metrics: list[dict[str, Any]] = []
    for cost_bp in cost_scenarios:
        net = portfolios.gross_return.to_numpy(dtype=float) - portfolios.exposure.to_numpy(dtype=float) * cost_bp / 10000.0
        stats = _period_stats(net)
        temp = portfolios.assign(net_return=net)
        temp["year"] = pd.to_datetime(temp.trade_date).dt.year
        temp["month"] = pd.to_datetime(temp.trade_date).dt.to_period("M").astype(str)
        monthly = temp.groupby("month", observed=True).net_return.apply(lambda x: np.prod(1 + x) - 1)
        yearly = temp.groupby("year", observed=True).net_return.apply(lambda x: np.prod(1 + x) - 1)
        metric = StrategyMetric(
            candidate_id=candidate_id,cost_bp=cost_bp,observation_count=len(work),
            valid_dates=len(daily),portfolio_periods=len(portfolios),
            trade_count=int(portfolios.selected_count.sum()),
            annualized_return=stats["annualized_return"],sharpe=stats["sharpe"],
            sortino=stats["sortino"],max_drawdown=stats["max_drawdown"],
            calmar=stats["calmar"],win_rate=stats["win_rate"],
            profit_factor=stats["profit_factor"],turnover=float(portfolios.exposure.mean()),
            average_holding_period=20.0,ic=_safe(daily.ic.mean()),
            rank_ic=_safe(rank_ic_mean),ic_ir=_safe(ic_ir),
            top_bottom_spread=_safe(daily.top_bottom_spread.mean()),
            tail_loss=stats["tail_loss"],monthly_stability=_safe((monthly > 0).mean()),
            yearly_stability=_safe((yearly > 0).mean()),mean_return=stats["mean_return"],
        )
        metrics.append(asdict(metric))
    return metrics, daily, portfolios


def period_metrics(portfolios: pd.DataFrame, candidate_id: str, cost_bp: int = 40) -> list[dict[str, Any]]:
    if portfolios.empty:
        return []
    temp = portfolios.copy()
    temp["net_return"] = temp.gross_return - temp.exposure * cost_bp / 10000.0
    date_values = pd.to_datetime(temp.trade_date)
    temp["year"] = date_values.dt.year.astype(str)
    temp["month"] = date_values.dt.to_period("M").astype(str)
    output: list[dict[str, Any]] = []
    for period_type, column in (("YEAR", "year"), ("MONTH", "month")):
        for period, group in temp.groupby(column, observed=True):
            stats = _period_stats(group.net_return.to_numpy(dtype=float))
            output.append(
                {
                    "candidate_id": candidate_id,"cost_bp": cost_bp,
                    "period_type": period_type,"period": period,"periods": len(group),**stats,
                }
            )
    return output


__all__ = ["evaluate_candidate", "period_metrics", "PRIMARY_COST_BP"]
