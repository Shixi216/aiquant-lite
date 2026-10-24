"""ETF 因子评分：只使用评分时点（含）之前的数据（防未来函数）。"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trading.wealth_lab.repository import WealthLabRepository


def score_factors(
    repository: WealthLabRepository,
    symbols: list[str],
    asof: date | None = None,
) -> dict:
    close = repository.read_close_matrix(symbols)
    if close.empty:
        return {"data_mode": "UNAVAILABLE", "reason": "数据池为空", "factors": []}
    if asof is not None:
        close = close.loc[: pd.Timestamp(asof)]
    if len(close) < 30:
        return {"data_mode": "UNAVAILABLE", "reason": f"历史不足（{len(close)} 天）", "factors": []}

    asof_date = close.index[-1].date().isoformat()
    rows = []
    for symbol in close.columns:
        series = close[symbol].dropna()
        if len(series) < 30:
            continue
        last = float(series.iloc[-1])
        momentum = last / float(series.iloc[-min(127, len(series))]) - 1.0 if len(series) >= 127 else None
        if momentum is None:
            momentum = last / float(series.iloc[0]) - 1.0
        sma_window = series.iloc[-min(200, len(series)):]
        trend = last / float(sma_window.mean()) - 1.0
        rets = series.pct_change().dropna().iloc[-63:]
        vol = float(rets.std() * np.sqrt(252)) if len(rets) > 10 else None
        window252 = series.iloc[-min(253, len(series)):]
        dd = float((window252 / window252.cummax() - 1.0).min())
        rows.append({
            "symbol": symbol, "asof": asof_date,
            "momentum_126d": round(momentum, 6),
            "trend_score": round(trend, 6),
            "volatility_63d": round(vol, 6) if vol is not None else None,
            "max_drawdown_252d": round(dd, 6),
            "liquidity_20d_avg_amount_cny": None,  # 由调用方补充（见 routes）
        })

    # 流动性（20 日日均成交额，元）
    liquidity = repository.liquidity_20d_avg(symbols, asof)
    for row in rows:
        row["liquidity_20d_avg_amount_cny"] = liquidity.get(row["symbol"])

    # 综合分位：动量/趋势/流动性 高者优，波动/回撤 绝对值低者优
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"data_mode": "UNAVAILABLE", "reason": "无有效资产", "factors": []}
    rank = (
        frame["momentum_126d"].rank()
        + frame["trend_score"].rank()
        + frame["liquidity_20d_avg_amount_cny"].fillna(0).rank()
        - frame["volatility_63d"].fillna(frame["volatility_63d"].max()).rank()
        - frame["max_drawdown_252d"].abs().rank()
    )
    # 冻结契约要求 0-1 分位：对原始加权秩得分再次做横截面百分位排名。
    frame["composite_rank"] = rank.rank(method="average", pct=True).round(4)
    frame = frame.sort_values("composite_rank", ascending=False)
    return {
        "data_mode": "REAL",
        "asof": asof_date,
        "factors": frame.to_dict("records"),
        "note": "研究排序用途，不构成投资建议，不修改股票正式评分",
    }
