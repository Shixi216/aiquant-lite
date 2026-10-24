"""Wealth Lab 回测引擎单元测试（纯计算，无数据库/网络）。"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.wealth_lab.backtest import (
    BacktestParams,
    compute_metrics,
    compute_rolling,
    generate_demo_close,
    run_backtest,
)

SYMS = ["A.SH", "B.SH"]


def _matrix(dates: pd.DatetimeIndex, paths: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(paths, index=dates)


def test_flat_prices_nav_is_one_with_zero_cost():
    idx = pd.bdate_range("2024-01-02", periods=40)
    frame = _matrix(idx, {s: [100.0] * 40 for s in SYMS})
    result = run_backtest(frame, BacktestParams(
        weights={"A.SH": 0.5, "B.SH": 0.5},
        start_date=idx[0].date(), end_date=idx[-1].date(),
        rebalance="monthly", cost_bps=0.0))
    assert result.nav[0] == pytest.approx(1.0)
    assert result.nav[-1] == pytest.approx(1.0)
    assert float(np.max(np.abs(result.drawdown))) == pytest.approx(0.0)


def test_initial_build_cost_deducted_once():
    idx = pd.bdate_range("2024-01-02", periods=5)
    frame = _matrix(idx, {s: [100.0] * 5 for s in SYMS})
    result = run_backtest(frame, BacktestParams(
        weights={"A.SH": 0.5, "B.SH": 0.5},
        start_date=idx[0].date(), end_date=idx[-1].date(),
        rebalance="none", cost_bps=10.0))
    # 建仓换手 Σ|Δw| = 1.0，成本 = 1.0 * 10bp = 1e-3，只扣一次
    assert result.nav[0] == pytest.approx(1.0 - 1e-3)
    assert result.nav[-1] == pytest.approx(1.0 - 1e-3)
    assert result.rebalances[0].cost == pytest.approx(1_000_000 * 1e-3)


def test_monthly_rebalance_resets_drift_and_charges_cost():
    # 1 月 A 涨 B 平 → 漂移；2 月首个交易日应回到 0.5/0.5 并扣成本
    idx = pd.bdate_range("2024-01-02", periods=45)
    a = [100.0] * 22 + [120.0] * 23  # 1月100 → 2月跳涨
    b = [100.0] * 45
    frame = _matrix(idx, {"A.SH": a, "B.SH": b})
    result = run_backtest(frame, BacktestParams(
        weights={"A.SH": 0.5, "B.SH": 0.5},
        start_date=idx[0].date(), end_date=idx[-1].date(),
        rebalance="monthly", cost_bps=5.0))
    first_february = next(i for i, d in enumerate(idx) if d.month == 2)
    assert idx[first_february].date() in {r.date for r in result.rebalances}
    feb = next(r for r in result.rebalances if r.date == idx[first_february].date())
    # 漂移后权重 = 0.5*1.2/(0.5*1.2+0.5) = 6/11 ≈ 0.5455
    trade_a = next(t for t in feb.trades if t["symbol"] == "A.SH")
    assert trade_a["weight_before"] == pytest.approx(6 / 11, abs=1e-6)
    assert feb.cost > 0


def test_metrics_on_known_series():
    idx = pd.bdate_range("2024-01-02", periods=252)
    # 每日 +0.1% → 年化 ≈ (1.001)^252 - 1
    frame = _matrix(idx, {s: [100.0 * (1.001 ** i) for i in range(252)] for s in SYMS})
    result = run_backtest(frame, BacktestParams(
        weights={"A.SH": 0.5, "B.SH": 0.5},
        start_date=idx[0].date(), end_date=idx[-1].date(),
        rebalance="monthly", cost_bps=0.0))
    metrics = compute_metrics(result)
    # 首日建仓无收益（pct_change 首日为 0），共 251 个 +0.1% 收益日
    assert metrics["total_return"] == pytest.approx(1.001 ** 251 - 1, rel=1e-9)
    # 首日 0 收益在样本内的离散度年化后约 0.1% 量级
    assert metrics["annual_volatility"] == pytest.approx(0.0, abs=2e-3)
    assert metrics["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
    assert metrics["trading_days"] == 252


def test_params_validation():
    with pytest.raises(ValueError):
        BacktestParams(weights={"A.SH": 0.6, "B.SH": 0.6},
                       start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)).validate()
    with pytest.raises(ValueError):
        BacktestParams(weights={"A.SH": 1.0},
                       start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
                       rebalance="weekly").validate()
    with pytest.raises(ValueError):
        BacktestParams(weights={"A.SH": 1.0},
                       start_date=date(2024, 12, 31), end_date=date(2024, 1, 1)).validate()


def test_rolling_and_demo_reproducible():
    demo = generate_demo_close(["510300.SH", "511010.SH", "518880.SH", "511380.SH"], days=400)
    demo2 = generate_demo_close(["510300.SH", "511010.SH", "518880.SH", "511380.SH"], days=400)
    pd.testing.assert_frame_equal(demo, demo2)  # 固定 seed 可复现
    result = run_backtest(demo, BacktestParams(
        weights={"510300.SH": 0.4, "511010.SH": 0.4, "518880.SH": 0.1, "511380.SH": 0.1},
        start_date=demo.index[0].date(), end_date=demo.index[-1].date(),
        rebalance="monthly", cost_bps=5.0))
    rolling = compute_rolling(result)
    assert len(rolling["volatility_63d"]) > 0
    assert len(rolling["dates"]) == len(rolling["volatility_63d"])
    assert rolling["value_at_risk_95_1d"] < 0
    assert len(result.dates) == len(result.nav) == len(result.drawdown)
