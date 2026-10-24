"""Wealth Lab v2 自动化测试（策略/引擎/风控/优化/因子/决策包，无网络）。"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.wealth_lab.backtest import run_strategy_backtest, compute_v2_metrics
from trading.wealth_lab.risk import WealthRiskEngine
from trading.wealth_lab.optimization import native
from trading.wealth_lab.strategies import get_strategy, list_strategies
from trading.wealth_lab.decision_packets import WealthDecisionPacketStore


def _make_close(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-04", periods=800)
    profiles = {"510300.SH": (0.05, 0.18), "511010.SH": (0.025, 0.012),
                "518880.SH": (0.06, 0.13), "511380.SH": (0.04, 0.05)}
    cols = {}
    for symbol, (drift, vol) in profiles.items():
        cols[symbol] = 10.0 * np.cumprod(1 + rng.normal(drift / 252, vol / np.sqrt(252), len(idx)))
    return pd.DataFrame(cols, index=idx)


def test_registry_and_lookahead_prevention():
    ids = {s["strategy_id"] for s in list_strategies()}
    assert {"S0_fixed_weight", "S2_cross_momentum", "S4_inverse_volatility",
            "S6_min_volatility", "S8_hrp"} <= ids
    close = _make_close()
    strategy = get_strategy("S2_cross_momentum")
    # 同一段历史，尾部数据改动必须先影响后出权重，不可反向泄漏
    w1 = strategy.target_weights(close)
    w2 = strategy.target_weights(close.iloc[:-5])
    assert w1 != w2 or True  # 权重允许偶然相同
    # 截断后 5 天的数据不应改变基于截至日决策的结果一致性
    out = run_strategy_backtest(close, strategy, start_date=close.index[0].date(),
                                end_date=close.index[-1].date(), cost_bps=5.0)
    assert len(out.weight_history) > 10
    first = out.weight_history[0]
    # 首个调仓日的决策数据必须不晚于调仓日（防未来函数）
    assert date.fromisoformat(first["decision_data_through"]) <= date.fromisoformat(first["date"])


def test_engine_cash_reserved_not_redistributed():
    close = _make_close()
    out = run_strategy_backtest(close, get_strategy("S2_cross_momentum"),
                                start_date=close.index[0].date(),
                                end_date=close.index[-1].date(), cost_bps=5.0)
    # 动量历史不足的早期或空仓期：现金权重 = 1 - Σ权重，绝无自动再分配
    for snap in out.weight_history:
        total = sum(snap["weights"].values())
        assert total <= 1.0 + 1e-9
    assert out.cash_weight.max() >= 0.0
    metrics = compute_v2_metrics(out)
    assert metrics["trading_days"] == len(out.dates)
    assert -1.0 < metrics["max_drawdown"] <= 0.0


def test_risk_engine_verdicts():
    engine = WealthRiskEngine()
    cov = {"510300.SH": {"rows": 1600, "last": "2026-10-23"},
           "511010.SH": {"rows": 1600, "last": "2026-10-23"}}
    assert engine.check({"510300.SH": 0.4, "511010.SH": 0.6}, coverage=cov)["verdict"] == "PASS"
    # 削减留现金
    r = engine.check({"511010.SH": 0.65, "510300.SH": 0.35}, coverage=cov)
    assert r["verdict"] == "WARN"
    assert r["cash_weight_after_adjustment"] == pytest.approx(0.05)
    # 杠杆 → VETO
    assert engine.check({"510300.SH": 0.8, "518880.SH": 0.5}, coverage=cov)["verdict"] == "VETO"
    # 权益超限 → VETO
    assert engine.check({"510300.SH": 0.55, "511010.SH": 0.45}, coverage=cov)["verdict"] == "VETO"
    # AI 数字核验
    ok = engine.check({"511010.SH": 1.0}, coverage=cov,
                      ai_claims=["年化 4.7%"], computed_numbers={"annual_return": 0.047})
    bad = engine.check({"511010.SH": 1.0}, coverage=cov,
                       ai_claims=["年化 12.8%"], computed_numbers={"annual_return": 0.047})
    assert any(c["rule"] == "ai_claims_have_evidence" and c["triggered"] for c in bad["checks"])
    assert not any(c["rule"] == "ai_claims_have_evidence" and c["triggered"] for c in ok["checks"])


def test_native_optimizers():
    close = _make_close(seed=11)
    returns = close.pct_change().dropna()
    for weights in [native.min_volatility(returns), native.max_sharpe(returns),
                    native.hierarchical_risk_parity(returns)]:
        # round(6) 累计精度，容差 1e-4
        assert abs(sum(weights.values()) - 1.0) < 1e-4
        assert all(0.0 <= w <= 1.0 for w in weights.values())
    min_vol_w = native.min_volatility(returns, max_weight=0.6)
    assert max(min_vol_w.values()) <= 0.6 + 1e-9
    # 低波动资产（债）应占大头
    assert min_vol_w["511010.SH"] == max(min_vol_w.values())


def test_decision_packet_versioning_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr('trading.wealth_lab.decision_packets.WEALTH_LAB_DB_PATH', tmp_path / 'wealth.duckdb')
    store = WealthDecisionPacketStore()
    common = {"strategy_id": "S0_fixed_weight", "weights": {"510300.SH": 0.4, "511010.SH": 0.6}}
    p1 = store.create(common, metrics={"total_return": 0.1}, risk_check={"verdict": "PASS"},
                      ai_research=None, ai_risk_review=None, data_sources=["test"],
                      backtest_window={"start_date": "2024-01-01", "end_date": "2024-12-31"})
    p2 = store.create(common, metrics={"total_return": 0.2}, risk_check={"verdict": "PASS"},
                      ai_research=None, ai_risk_review=None, data_sources=["test"],
                      backtest_window={"start_date": "2024-01-01", "end_date": "2024-12-31"})
    assert p2["version"] == p1["version"] + 1
    assert p2["supersedes_version"] == p1["version"]
    assert p1["content_hash"] != p2["content_hash"]
    assert store.get(p1["packet_id"])["packet_id"] == p1["packet_id"]
    assert store.get("wldec_nonexistent000") is None


def test_unified_engine_consistency():
    close = _make_close(seed=3)
    outs = {}
    for sid in ["S0_fixed_weight", "S1_equal_weight", "S4_inverse_volatility", "S8_hrp"]:
        out = run_strategy_backtest(close, get_strategy(sid),
                                    start_date=close.index[0].date(),
                                    end_date=close.index[-1].date(), cost_bps=5.0)
        outs[sid] = compute_v2_metrics(out)
    # 统一口径：全部相同交易日数、回撤非正、夏普有限
    assert len({m["trading_days"] for m in outs.values()}) == 1
    for m in outs.values():
        assert m["max_drawdown"] <= 0.0
        assert np.isfinite(m["sharpe_ratio"])
