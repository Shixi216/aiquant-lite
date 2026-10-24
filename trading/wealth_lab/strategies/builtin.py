"""内置策略 S0-S5（中国场内基金，全部只用决策时点前的历史数据）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trading.wealth_lab.config import DEFAULT_WEIGHTS
from trading.wealth_lab.strategies.base import Strategy, StrategyMeta, register_strategy


@register_strategy
class FixedWeightStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S0_fixed_weight", strategy_version="1.0", name="固定权重配置",
        description="经典股债金 40/40/10/10，月度再平衡",
        params={"weights": DEFAULT_WEIGHTS}, min_history_days=2, rebalance="monthly",
        risk_notes="", asset_selection="全部资产固定权重",
    )

    def apply_params(self, params: dict) -> None:
        self._weights = params["weights"]

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        return self.validate_output(self._weights, list(history.columns))


@register_strategy
class EqualWeightStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S1_equal_weight", strategy_version="1.0", name="等权配置",
        description="等权持有全部资产", params={}, min_history_days=2, rebalance="monthly",
        asset_selection="全部资产等权",
    )

    def apply_params(self, params: dict) -> None:
        self._max_weight = params.get("max_weight", 1.0)

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        cols = list(history.columns)
        w = {s: 1.0 / len(cols) for s in cols}
        return self.validate_output(w, cols)


@register_strategy
class CrossMomentumStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S2_cross_momentum", strategy_version="1.0", name="ETF横截面动量轮动",
        description="按 lookback 日动量排序持有前 top_k 名；动量<min_momentum 的不持有（留现金）",
        params={"lookback_days": 126, "top_k": 2, "min_momentum": 0.0},
        min_history_days=130, rebalance="monthly",
        risk_notes="动量崩溃风险；趋势反转时回撤可能加大；历史不足时持币",
        asset_selection="横截面动量排序取前2",
    )

    def apply_params(self, params: dict) -> None:
        self._lookback = int(params["lookback_days"])
        self._top_k = int(params["top_k"])
        self._min_momentum = float(params["min_momentum"])

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        if len(history) < self._lookback + 1:
            return {}  # 历史不足 → 全现金（引擎按现金处理）
        window = history.iloc[-(self._lookback + 1):]
        momentum = window.iloc[-1] / window.iloc[0] - 1.0
        ranked = momentum[momentum > self._min_momentum].sort_values(ascending=False)
        picked = list(ranked.index[: self._top_k])
        if not picked:
            return {}
        weight = 1.0 / len(picked)
        return self.validate_output({s: weight for s in picked}, list(history.columns))


@register_strategy
class DualMomentumStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S3_dual_momentum", strategy_version="1.0", name="双动量防御",
        description="相对动量在 risky 中选最强；其绝对动量<0 时切换 defensive",
        params={"lookback_days": 252, "defensive": "511010.SH", "risky": ["510300.SH", "518880.SH"]},
        min_history_days=256, rebalance="monthly",
        risk_notes="震荡市频繁切换；单一进攻资产集中风险",
        asset_selection="risky 中动量最强者，否则 defensive",
    )

    def apply_params(self, params: dict) -> None:
        self._lookback = int(params["lookback_days"])
        self._defensive = params["defensive"]
        self._risky = list(params["risky"])

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        universe = [s for s in [self._defensive, *self._risky] if s in history.columns]
        if len(history) < self._lookback + 1:
            return {self._defensive: 1.0} if self._defensive in history.columns else {}
        window = history.iloc[-(self._lookback + 1):]
        momentum = window.iloc[-1] / window.iloc[0] - 1.0
        risky = [s for s in self._risky if s in history.columns]
        if not risky:
            return {self._defensive: 1.0} if self._defensive in history.columns else {}
        best = max(risky, key=lambda s: momentum.get(s, -np.inf))
        if momentum.get(best, -np.inf) > 0:
            return self.validate_output({best: 1.0}, universe)
        return {self._defensive: 1.0} if self._defensive in history.columns else {}


@register_strategy
class InverseVolatilityStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S4_inverse_volatility", strategy_version="1.0", name="波动率倒数配置",
        description="window 日波动率倒数加权并归一（低波动超配）",
        params={"window_days": 63, "max_weight": 0.6}, min_history_days=65, rebalance="monthly",
        risk_notes="低波动资产长期超配；极端行情波动骤变",
        asset_selection="全部资产",
    )

    def apply_params(self, params: dict) -> None:
        self._window = int(params["window_days"])
        self._max_weight = float(params["max_weight"])

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        if len(history) < self._window + 1:
            cols = list(history.columns)
            return self.validate_output({s: 1.0 / len(cols) for s in cols}, cols)
        vol = history.iloc[-(self._window + 1):].pct_change().dropna().std()
        inv = (1.0 / vol.replace(0, np.nan)).fillna(0.0)
        if inv.sum() <= 0:
            cols = list(history.columns)
            return self.validate_output({s: 1.0 / len(cols) for s in cols}, cols)
        raw = inv / inv.sum()
        capped = raw.clip(upper=self._max_weight)
        if capped.sum() > 0:
            capped = capped / capped.sum()
        return self.validate_output(dict(capped), list(history.columns))


@register_strategy
class TrendDefenseStrategy(Strategy):
    meta = StrategyMeta(
        strategy_id="S5_trend_defense", strategy_version="1.0", name="均线趋势防御",
        description="价格高于 SMA{sma} 的资产持目标权重；破位资产的份额转入 defensive",
        params={"sma_days": 200, "defensive": "511010.SH", "weights": DEFAULT_WEIGHTS},
        min_history_days=202, rebalance="monthly",
        risk_notes="均线滞后；盘整市反复切换",
        asset_selection="全部资产，趋势破位部分转防御",
    )

    def apply_params(self, params: dict) -> None:
        self._sma = int(params["sma_days"])
        self._defensive = params["defensive"]
        self._weights = params["weights"]

    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        target = {s: w for s, w in self._weights.items() if s in history.columns}
        if len(history) < self._sma:
            return self.validate_output(target, list(history.columns))
        sma = history.iloc[-self._sma:].mean()
        last = history.iloc[-1]
        output: dict[str, float] = {}
        defensive_extra = 0.0
        for symbol, weight in target.items():
            if symbol == self._defensive:
                output[symbol] = output.get(symbol, 0.0) + weight
                continue
            if symbol in sma.index and pd.notna(sma[symbol]) and last[symbol] >= sma[symbol]:
                output[symbol] = output.get(symbol, 0.0) + weight
            else:
                defensive_extra += weight
        if defensive_extra > 0:
            if self._defensive in history.columns:
                output[self._defensive] = output.get(self._defensive, 0.0) + defensive_extra
            else:  # 防御资产不在池内时保留为现金
                total = sum(output.values())
                if total > 0:
                    output = {s: w * (1.0 - defensive_extra) / total for s, w in output.items()}
        return self.validate_output(output, list(history.columns))
