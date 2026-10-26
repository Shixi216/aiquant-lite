"""统一策略接口与注册表。

防未来函数约束（强制）：
- 引擎只在调仓日传入截至**前一交易日**的历史数据（close.loc[:decision_date]），
  策略内部任何估计（动量/均线/波动/协方差）只能使用该窗口；
- 优化型策略的参数估计窗口与之后的验证窗口由引擎严格分离。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class StrategyMeta:
    strategy_id: str
    strategy_version: str
    name: str
    description: str
    params: dict = field(default_factory=dict)
    min_history_days: int = 20
    rebalance: str = "monthly"
    risk_notes: str = ""
    asset_selection: str = ""


class Strategy(ABC):
    """所有策略实现同一接口；target_weights 只依赖传入历史窗口。"""

    meta: StrategyMeta

    @abstractmethod
    def target_weights(self, history: pd.DataFrame) -> dict[str, float]:
        """输入：截至决策时点（含）的历史收盘矩阵；输出：目标权重（和 ≤ 1，余为现金）。"""

    def describe(self) -> dict:
        m = self.meta
        return {
            "strategy_id": m.strategy_id,
            "strategy_version": m.strategy_version,
            "name": m.name,
            "description": m.description,
            "params": m.params,
            "min_history_days": m.min_history_days,
            "rebalance": m.rebalance,
            "risk_notes": m.risk_notes,
            "asset_selection": m.asset_selection,
        }

    def validate_output(self, weights: dict[str, float], universe: list[str]) -> dict[str, float]:
        cleaned = {s: float(min(max(w, 0.0), 1.0)) for s, w in weights.items() if s in universe and w > 0}
        total = sum(cleaned.values())
        if total > 1.0 + 1e-9:
            cleaned = {s: w / total for s, w in cleaned.items()}
        return cleaned


_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.meta.strategy_id] = cls
    return cls


def list_strategies() -> list[dict]:
    return [cls().describe() for _, cls in sorted(_REGISTRY.items())]


def get_strategy(strategy_id: str, **overrides) -> Strategy:
    if strategy_id not in _REGISTRY:
        raise ValueError(f"unknown strategy: {strategy_id}")
    meta = _REGISTRY[strategy_id].meta
    params = {**meta.params, **overrides}
    instance = _REGISTRY[strategy_id]()
    object.__setattr__(
        instance, "meta",
        StrategyMeta(**{**meta.__dict__, "params": params}),
    )
    instance.apply_params(params)
    return instance
