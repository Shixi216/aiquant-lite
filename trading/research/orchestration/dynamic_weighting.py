"""动态权重分配引擎 (Dynamic Weighting Engine) - Phase S4B

基于历史回放与实盘表现数据，自适应计算 Technical、Fundamental、Sentiment 3 因子的正式合成权重。
具备：
1. IC 加权法 (IC-weighted) 与 逆波动率加权法 (Inverse Volatility)
2. 安全边界保护 (Safety Clamping): Tech [0.35, 0.70], Fund [0.20, 0.50], Sentiment [0.05, 0.30]
3. 权重漂移平滑 (Drift Smoothing): 限制相邻交易日单因子权重变动 <= 2%
4. 优雅回退 (Graceful Fallback): 数据不足时平滑回退至基准 (50/35/15)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from data_hub.schemas.unified import FactorType

DYNAMIC_WEIGHTING_VERSION = "dynamic-weighting-v1.0.0"

# 默认安全边界
DEFAULT_WEIGHT_BOUNDS: dict[FactorType, tuple[float, float]] = {
    FactorType.TECHNICAL: (0.35, 0.70),
    FactorType.FUNDAMENTAL: (0.20, 0.50),
    FactorType.SENTIMENT: (0.05, 0.30),
}

# 默认基准权重
DEFAULT_BASELINE_WEIGHTS: dict[FactorType, float] = {
    FactorType.TECHNICAL: 0.50,
    FactorType.FUNDAMENTAL: 0.35,
    FactorType.SENTIMENT: 0.15,
}

# 默认最大日漂移量
DEFAULT_MAX_DAILY_DRIFT = 0.02


class WeightingMethod(StrEnum):
    IC_WEIGHTED = "IC_WEIGHTED"
    INVERSE_VOLATILITY = "INVERSE_VOLATILITY"
    STATIC_BASELINE = "STATIC_BASELINE"


@dataclass
class DynamicWeightResult:
    """动态权重计算结果"""
    weights: dict[FactorType, float]
    method: WeightingMethod
    lookback_days: int
    ic_values: dict[FactorType, float] = field(default_factory=dict)
    volatilities: dict[FactorType, float] = field(default_factory=dict)
    clamped: bool = False
    smoothed: bool = False
    algorithm_version: str = DYNAMIC_WEIGHTING_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {k.value: v for k, v in self.weights.items()},
            "method": self.method.value,
            "lookback_days": self.lookback_days,
            "ic_values": {k.value: v for k, v in self.ic_values.items()},
            "volatilities": {k.value: v for k, v in self.volatilities.items()},
            "clamped": self.clamped,
            "smoothed": self.smoothed,
            "algorithm_version": self.algorithm_version,
        }


class DynamicWeightCalculator:
    """动态权重计算器"""

    def __init__(
        self,
        bounds: dict[FactorType, tuple[float, float]] | None = None,
        baseline_weights: dict[FactorType, float] | None = None,
        max_daily_drift: float = DEFAULT_MAX_DAILY_DRIFT,
    ) -> None:
        self.bounds = bounds or DEFAULT_WEIGHT_BOUNDS
        self.baseline_weights = baseline_weights or DEFAULT_BASELINE_WEIGHTS
        self.max_daily_drift = max_daily_drift

    def compute_from_ics(
        self,
        factor_ics: dict[FactorType, float],
        lookback_days: int = 60,
        previous_weights: dict[FactorType, float] | None = None,
    ) -> DynamicWeightResult:
        """基于各因子的 RankIC 计算动态权重
        
        Args:
            factor_ics: 各因子的历史平均 RankIC (正值表示正向预测力)
            lookback_days: 回看窗口天数
            previous_weights: 前一交易日权重（用于平滑）
        """
        # 若 IC 缺失或全部 <= 0，回退至基准权重
        valid_ics = {k: max(0.001, v) for k, v in factor_ics.items() if k in self.baseline_weights}
        if not valid_ics or sum(valid_ics.values()) <= 0:
            return DynamicWeightResult(
                weights=dict(self.baseline_weights),
                method=WeightingMethod.STATIC_BASELINE,
                lookback_days=lookback_days,
                ic_values=factor_ics,
            )

        # 1. 初始比例分配: w_i = IC_i / sum(IC)
        total_ic = sum(valid_ics.values())
        raw_weights = {k: v / total_ic for k, v in valid_ics.items()}
        
        # 补齐未出现在 IC 中的基准因子
        for f in self.baseline_weights:
            if f not in raw_weights:
                raw_weights[f] = self.baseline_weights[f]
        
        # 重新归一化
        sum_raw = sum(raw_weights.values())
        raw_weights = {k: v / sum_raw for k, v in raw_weights.items()}

        # 2. 边界裁剪 (Clamping)
        clamped_weights, was_clamped = self._clamp_and_normalize(raw_weights)

        # 3. 漂移平滑 (Smoothing)
        final_weights = clamped_weights
        was_smoothed = False
        if previous_weights:
            final_weights, was_smoothed = self._smooth_drift(previous_weights, clamped_weights)

        return DynamicWeightResult(
            weights=final_weights,
            method=WeightingMethod.IC_WEIGHTED,
            lookback_days=lookback_days,
            ic_values=factor_ics,
            clamped=was_clamped,
            smoothed=was_smoothed,
        )

    def compute_from_volatilities(
        self,
        factor_vols: dict[FactorType, float],
        lookback_days: int = 60,
        previous_weights: dict[FactorType, float] | None = None,
    ) -> DynamicWeightResult:
        """基于各因子得分的逆波动率 (1 / sigma) 计算动态权重
        
        波动率越低（越稳定），分配的权重越大。
        """
        inv_vols = {}
        for f, vol in factor_vols.items():
            if f in self.baseline_weights and vol > 1e-6 and math.isfinite(vol):
                inv_vols[f] = 1.0 / vol
            elif f in self.baseline_weights:
                inv_vols[f] = 1.0 / 0.1  # 默认兜底

        if not inv_vols:
            return DynamicWeightResult(
                weights=dict(self.baseline_weights),
                method=WeightingMethod.STATIC_BASELINE,
                lookback_days=lookback_days,
                volatilities=factor_vols,
            )

        total_inv_vol = sum(inv_vols.values())
        raw_weights = {k: v / total_inv_vol for k, v in inv_vols.items()}

        for f in self.baseline_weights:
            if f not in raw_weights:
                raw_weights[f] = self.baseline_weights[f]

        sum_raw = sum(raw_weights.values())
        raw_weights = {k: v / sum_raw for k, v in raw_weights.items()}

        clamped_weights, was_clamped = self._clamp_and_normalize(raw_weights)

        final_weights = clamped_weights
        was_smoothed = False
        if previous_weights:
            final_weights, was_smoothed = self._smooth_drift(previous_weights, clamped_weights)

        return DynamicWeightResult(
            weights=final_weights,
            method=WeightingMethod.INVERSE_VOLATILITY,
            lookback_days=lookback_days,
            volatilities=factor_vols,
            clamped=was_clamped,
            smoothed=was_smoothed,
        )

    def _clamp_and_normalize(
        self,
        raw_weights: dict[FactorType, float],
    ) -> tuple[dict[FactorType, float], bool]:
        """将权重裁剪至安全边界内并重新归一化至 sum = 1.0"""
        clamped = {}
        was_clamped = False

        for f, w in raw_weights.items():
            if f in self.bounds:
                min_w, max_w = self.bounds[f]
                if w < min_w:
                    clamped[f] = min_w
                    was_clamped = True
                elif w > max_w:
                    clamped[f] = max_w
                    was_clamped = True
                else:
                    clamped[f] = w
            else:
                clamped[f] = w

        # 迭代调整归一化以保证同时满足边界和 sum = 1.0
        total = sum(clamped.values())
        if abs(total - 1.0) > 1e-6:
            diff = 1.0 - total
            adjustable = {}
            for f, w in clamped.items():
                min_w, max_w = self.bounds.get(f, (0.0, 1.0))
                if diff > 0 and w < max_w:
                    adjustable[f] = max_w - w
                elif diff < 0 and w > min_w:
                    adjustable[f] = w - min_w

            if adjustable and sum(adjustable.values()) > 0:
                total_adj = sum(adjustable.values())
                for f, room in adjustable.items():
                    clamped[f] += diff * (room / total_adj)
            else:
                n = len(clamped)
                for f in clamped:
                    clamped[f] += diff / n

        rounded = {f: round(w, 4) for f, w in clamped.items()}
        remainder = round(1.0 - sum(rounded.values()), 4)
        if FactorType.TECHNICAL in rounded:
            rounded[FactorType.TECHNICAL] += remainder
        elif rounded:
            first_key = next(iter(rounded))
            rounded[first_key] += remainder

        return rounded, was_clamped

    def _smooth_drift(
        self,
        previous_weights: dict[FactorType, float],
        target_weights: dict[FactorType, float],
    ) -> tuple[dict[FactorType, float], bool]:
        """限制单日权重漂移不超过 max_daily_drift"""
        smoothed = {}
        was_smoothed = False

        for f, target in target_weights.items():
            prev = previous_weights.get(f, target)
            delta = target - prev
            if abs(delta) > self.max_daily_drift:
                was_smoothed = True
                if delta > 0:
                    smoothed[f] = prev + self.max_daily_drift
                else:
                    smoothed[f] = prev - self.max_daily_drift
            else:
                smoothed[f] = target

        total = sum(smoothed.values())
        if abs(total - 1.0) > 1e-6:
            smoothed = {f: w / total for f, w in smoothed.items()}

        rounded = {f: round(w, 4) for f, w in smoothed.items()}
        remainder = round(1.0 - sum(rounded.values()), 4)
        if FactorType.TECHNICAL in rounded:
            rounded[FactorType.TECHNICAL] += remainder
        elif rounded:
            first_key = next(iter(rounded))
            rounded[first_key] += remainder

        return rounded, was_smoothed


__all__ = [
    "DEFAULT_BASELINE_WEIGHTS",
    "DEFAULT_MAX_DAILY_DRIFT",
    "DEFAULT_WEIGHT_BOUNDS",
    "DYNAMIC_WEIGHTING_VERSION",
    "DynamicWeightCalculator",
    "DynamicWeightResult",
    "WeightingMethod",
]
