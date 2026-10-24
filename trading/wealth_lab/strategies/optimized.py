"""优化型策略 S6-S8：滚动估计窗口 + 适配器优化（防未来函数：只用截至决策时点数据）。"""
from __future__ import annotations

import pandas as pd

from trading.wealth_lab.optimization import optimize_portfolio
from trading.wealth_lab.strategies.base import Strategy, StrategyMeta, register_strategy


class _OptimizedStrategy(Strategy):
    _method: str = ""

    def apply_params(self, params: dict) -> None:
        self._window = int(params["window_days"])
        self._max_weight = float(params["max_weight"])
        self._rf = float(params.get("risk_free_rate", 0.02))
        self._adapter = params.get("adapter", "auto")

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        cols = list(history.columns)
        if len(history) < self._window:
            return self.validate_output({s: 1.0 / len(cols) for s in cols}, cols)
        window = history.iloc[-(self._window + 1):]
        returns = window.pct_change().dropna()
        if len(returns) < 20:
            return self.validate_output({s: 1.0 / len(cols) for s in cols}, cols)
        try:
            result = optimize_portfolio(
                self._method, returns,
                risk_free_rate=self._rf, max_weight=self._max_weight, adapter=self._adapter,
            )
        except Exception:
            return self.validate_output({s: 1.0 / len(cols) for s in cols}, cols)
        return self.validate_output(result["weights"], cols)


@register_strategy
class MinVolatilityStrategy(_OptimizedStrategy):
    meta = StrategyMeta(
        strategy_id="S6_min_volatility", strategy_version="1.0", name="最小波动率优化",
        description="滚动 window 日最小方差组合（长仓，单资产上限 max_weight）",
        params={"window_days": 252, "max_weight": 0.6}, min_history_days=256, rebalance="monthly",
        risk_notes="协方差估计误差；窗口依赖", asset_selection="全部资产，优化器分配",
    )
    _method = "min_vol"


@register_strategy
class MaxSharpeStrategy(_OptimizedStrategy):
    meta = StrategyMeta(
        strategy_id="S7_max_sharpe", strategy_version="1.0", name="最大夏普优化",
        description="滚动 window 日最大夏普比率组合",
        params={"window_days": 252, "max_weight": 0.6, "risk_free_rate": 0.02},
        min_history_days=256, rebalance="monthly",
        risk_notes="对收益估计极敏感，易集中于单一资产", asset_selection="全部资产，优化器分配",
    )
    _method = "max_sharpe"


@register_strategy
class HrpStrategy(_OptimizedStrategy):
    meta = StrategyMeta(
        strategy_id="S8_hrp", strategy_version="1.0", name="层次风险平价HRP",
        description="相关矩阵层次聚类递归二分分配（Lopez de Prado 2016 思路的独立实现）",
        params={"window_days": 252, "max_weight": 1.0}, min_history_days=256, rebalance="monthly",
        risk_notes="不依赖收益估计，仍受窗口影响", asset_selection="全部资产，聚类分配",
    )
    _method = "hrp"
