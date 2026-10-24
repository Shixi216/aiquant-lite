"""QuantStats 等效风险报告（原生实现，口径注明）。

说明：quantstats 与项目 pandas 3.0.3/numpy 2.4.6 不兼容（旧 API），
本模块按其公开指标口径（annualized return/vol、Sharpe、Sortino、
Calmar、月度收益热力矩阵、滚动统计、最差交易日）独立实现，
作为风险分析扩展与独立核验工具，不替换 v1 指标体系。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from trading.wealth_lab.backtest import compute_metrics, compute_v2_metrics
from trading.wealth_lab.config import RF_ANNUAL, TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from trading.wealth_lab.backtest import StrategyBacktestOutput

ENGINE = "native-equivalent(quantstats-compatible)"


def build_report(output: "StrategyBacktestOutput", backtest_id: str) -> dict:
    metrics = compute_v2_metrics(output)
    dates = pd.to_datetime(output.dates)
    nav = pd.Series(output.nav, index=dates)
    monthly = nav.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()
    first_month = nav.resample("ME").first().dropna()
    monthly_ret = pd.concat([first_month.pct_change().dropna().iloc[:0], monthly_ret])  # 首月以月末计
    # 首月收益相对月初（即首日 nav[0]）
    first_month_ret = monthly.iloc[0] / nav.iloc[0] - 1.0
    matrix: dict[str, dict[str, float | None]] = {}
    if not np.isnan(first_month_ret):
        ym = str(monthly.index[0].year)
        matrix.setdefault(ym, {})[f"{monthly.index[0].month:02d}"] = round(float(first_month_ret), 6)
    for idx, value in monthly_ret.items():
        matrix.setdefault(str(idx.year), {})[f"{idx.month:02d}"] = round(float(value), 6)

    daily = pd.Series(output.daily_returns, index=dates)
    rolling_vol = (daily.rolling(63).std() * np.sqrt(TRADING_DAYS_PER_YEAR)).iloc[-1]
    rolling_sharpe = (
        (daily.rolling(252).mean() * TRADING_DAYS_PER_YEAR - RF_ANNUAL)
        / (daily.rolling(252).std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    ).iloc[-1]
    worst_idx = np.argsort(output.daily_returns)[:5]
    return {
        "backtest_id": backtest_id,
        "engine": ENGINE,
        "metrics": metrics,
        "monthly_returns": matrix,
        "rolling": {
            "volatility_63d_last": round(float(rolling_vol), 6) if pd.notna(rolling_vol) else None,
            "sharpe_252d_last": round(float(rolling_sharpe), 6) if pd.notna(rolling_sharpe) else None,
        },
        "worst_days": sorted(
            [
                {"date": output.dates[i].isoformat(), "daily_return": round(float(output.daily_returns[i]), 6)}
                for i in worst_idx
            ],
            key=lambda item: item["daily_return"],
        ),
        "html_available": True,
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Wealth Lab 风险报告 {bid}</title>
<style>
body{{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:24px;color:#1a1a2e;background:#fafafa}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:24px}}
table{{border-collapse:collapse;font-size:12px}} td,th{{border:1px solid #ddd;padding:3px 7px;text-align:right}}
.pos{{background:#e8f5e9}} .neg{{background:#fdecea}} .muted{{color:#777;font-size:11px}}
</style></head><body>
<h1>怡怡投研·大类资产配置实验室 — 风险报告</h1>
<p class="muted">backtest_id: {bid} · 引擎: {engine} · 研究用途，不构成投资建议</p>
<h2>核心指标</h2><table><tr><th>指标</th><th>数值</th></tr>{metric_rows}</table>
<h2>月度收益矩阵</h2><table><tr><th>年</th>{month_header}</tr>{month_rows}</table>
<h2>最差交易日</h2><table><tr><th>日期</th><th>日收益</th></tr>{worst_rows}</table>
<p class="muted">口径：SSE 交易日历；CNY；fund_adj 后复权；Sharpe rf=2%；Sortino 下行波动年化。
quantstats 与 pandas 3.0 不兼容，本报告为同口径原生实现。</p>
</body></html>"""


def render_html(report: dict) -> str:
    metrics = report["metrics"]
    metric_rows = "".join(
        f"<tr><td>{k}</td><td>{v:.4f}</td></tr>" if isinstance(v, float) else f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in metrics.items()
    )
    months = [f"{m:02d}" for m in range(1, 13)]
    month_header = "".join(f"<th>{m}</th>" for m in months)
    month_rows = ""
    for year in sorted(report["monthly_returns"]):
        cells = ""
        row = report["monthly_returns"][year]
        for m in months:
            value = row.get(m)
            if value is None:
                cells += "<td></td>"
            else:
                cells += f'<td class="{"pos" if value >= 0 else "neg"}">{value*100:.2f}%</td>'
        month_rows += f"<tr><th>{year}</th>{cells}</tr>"
    worst_rows = "".join(
        f'<tr><td>{d["date"]}</td><td class="neg">{d["daily_return"]*100:.2f}%</td></tr>'
        for d in report["worst_days"]
    )
    return _HTML_TEMPLATE.format(
        bid=report["backtest_id"], engine=report["engine"],
        metric_rows=metric_rows, month_header=month_header,
        month_rows=month_rows, worst_rows=worst_rows,
    )
