"""优化适配器：优先 pypfopt（若可用），否则 native 纯 numpy 实现。

统一输出：{method, adapter_used, weights, stats}，屏蔽底层差异；
调用方（策略/路由）不感知具体库。第三方归属在文档与本文件注明。
"""
from __future__ import annotations

import pandas as pd

from trading.wealth_lab.optimization import native

try:  # pypfopt 与 pandas 3.0/numpy 2.x 兼容性未验证，仅在其可导入时启用
    from pypfopt import EfficientFrontier, HRPOpt, risk_models, expected_returns

    PYPFOPT_AVAILABLE = True
except Exception:  # pragma: no cover
    PYPFOPT_AVAILABLE = False


def optimize_portfolio(
    method: str,
    returns: pd.DataFrame,
    *,
    risk_free_rate: float = 0.02,
    max_weight: float = 0.6,
    adapter: str = "auto",
) -> dict:
    if method not in {"min_vol", "max_sharpe", "hrp"}:
        raise ValueError(f"unknown optimization method: {method}")
    use_pypfopt = adapter == "pypfopt" or (adapter == "auto" and PYPFOPT_AVAILABLE)
    if use_pypfopt and method != "hrp":
        weights = _pypfopt_ef(method, returns, risk_free_rate, max_weight)
        adapter_used = "pypfopt"
    elif use_pypfopt and method == "hrp":
        weights = _pypfopt_hrp(returns)
        adapter_used = "pypfopt"
    else:
        if method == "min_vol":
            weights = native.min_volatility(returns, max_weight=max_weight)
        elif method == "max_sharpe":
            weights = native.max_sharpe(returns, risk_free_rate=risk_free_rate, max_weight=max_weight)
        else:
            weights = native.hierarchical_risk_parity(returns, max_weight=max_weight)
        adapter_used = "native"
    stats = native.estimate_stats(returns, weights, risk_free_rate)
    return {"method": method, "adapter_used": adapter_used, "weights": weights, "stats": stats}


def _pypfopt_ef(method: str, returns: pd.DataFrame, rf: float, cap: float) -> dict[str, float]:
    mu = expected_returns.mean_historical_return(returns)
    cov = risk_models.sample_cov(returns)
    ef = EfficientFrontier(mu, cov, weight_bounds=(0.0, cap))
    ef.min_volatility() if method == "min_vol" else ef.max_sharpe(risk_free_rate=rf)
    return {k: round(float(v), 6) for k, v in ef.clean_weights().items() if v > 0}


def _pypfopt_hrp(returns: pd.DataFrame) -> dict[str, float]:
    hrp = HRPOpt(returns)
    return {k: round(float(v), 6) for k, v in hrp.optimize().items() if v > 0}
