from __future__ import annotations

import math
from dataclasses import dataclass

from config.settings import settings
from trading.research.capital_flow.policy import scoring_weights
from trading.research.capital_flow.schemas import (
    FinancingTrend,
    LiquidityLevel,
    PriceVolumeState,
    SubScore,
)


@dataclass(frozen=True)
class ScoreResult:
    score: float
    confidence: float
    sub_scores: dict[str, SubScore]


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


def ratio_score(value: float | None, confidence: float) -> SubScore:
    if value is None or value <= 0:
        return SubScore(value=None, confidence=0, available=False)
    return SubScore(
        value=_clip(math.log(value, 2) / 2),
        confidence=confidence,
        available=True,
    )


def turnover_score(percentile: float | None, rate: float | None) -> SubScore:
    if rate is None:
        return SubScore(value=None, confidence=0, available=False)
    if rate >= settings.capital_flow_high_turnover_threshold:
        value = -0.5
    elif percentile is None:
        value = 0.0
    else:
        value = _clip((percentile - 0.5) * 1.2)
    return SubScore(value=value, confidence=0.7, available=True)


def price_volume_score(state: PriceVolumeState) -> SubScore:
    values = {
        PriceVolumeState.PRICE_UP_VOLUME_UP: 0.6,
        PriceVolumeState.PRICE_UP_VOLUME_DOWN: -0.15,
        PriceVolumeState.PRICE_DOWN_VOLUME_UP: -0.7,
        PriceVolumeState.PRICE_DOWN_VOLUME_DOWN: -0.2,
        PriceVolumeState.PRICE_FLAT_VOLUME_UP: 0.0,
        PriceVolumeState.PRICE_FLAT_VOLUME_DOWN: 0.0,
        PriceVolumeState.PRICE_VOLUME_NEUTRAL: 0.0,
    }
    if state == PriceVolumeState.UNKNOWN:
        return SubScore(value=None, confidence=0, available=False)
    return SubScore(value=values[state], confidence=0.75, available=True)


def financing_score(trend: FinancingTrend) -> SubScore:
    values = {
        FinancingTrend.RISING: 0.15,
        FinancingTrend.FALLING: -0.15,
        FinancingTrend.STABLE: 0.0,
        FinancingTrend.VOLATILE: -0.25,
    }
    if trend == FinancingTrend.MISSING:
        return SubScore(value=None, confidence=0, available=False)
    return SubScore(value=values[trend], confidence=0.5, available=True)


def liquidity_score(level: LiquidityLevel, confidence: float) -> SubScore:
    values = {
        LiquidityLevel.HIGH: 0.4,
        LiquidityLevel.MEDIUM: 0.0,
        LiquidityLevel.LOW: -0.5,
        LiquidityLevel.ILLIQUID: -1.0,
    }
    if level == LiquidityLevel.UNKNOWN:
        return SubScore(value=None, confidence=0, available=False)
    return SubScore(value=values[level], confidence=confidence, available=True)


def combine_sub_scores(sub_scores: dict[str, SubScore]) -> ScoreResult:
    weights = scoring_weights()
    available = {
        key: value
        for key, value in sub_scores.items()
        if value.available and value.value is not None and value.confidence > 0
    }
    if not available:
        return ScoreResult(0.0, 0.0, sub_scores)
    raw_weights = {
        key: weights[key] * item.confidence
        for key, item in available.items()
    }
    raw_total = sum(raw_weights.values())
    contribution_cap = (
        settings.capital_flow_max_single_component_contribution * raw_total
    )
    effective_weights = {
        key: min(value, contribution_cap)
        for key, value in raw_weights.items()
    }
    denominator = sum(effective_weights.values())
    score = (
        sum(
            (available[key].value or 0) * weight
            for key, weight in effective_weights.items()
        )
        / denominator
    )
    configured_total = sum(weights.values())
    confidence = min(
        1.0,
        sum(raw_weights.values()) / configured_total
        if configured_total
        else 0.0,
    )
    return ScoreResult(_clip(score), confidence, sub_scores)


__all__ = [
    "ScoreResult",
    "combine_sub_scores",
    "financing_score",
    "liquidity_score",
    "price_volume_score",
    "ratio_score",
    "turnover_score",
]
