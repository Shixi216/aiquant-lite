from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from config.settings import settings
from trading.research.policy_news.policy import POLICY_NEWS_SCORER_VERSION
from trading.research.policy_news.schemas import (
    ImplementationStatus,
    PolicyImpactHorizon,
    PolicyVerificationStatus,
)


@dataclass(frozen=True)
class PolicyNewsScore:
    score: float
    confidence: float
    missing_components: tuple[str, ...]
    algorithm_version: str = POLICY_NEWS_SCORER_VERSION


def freshness_weight(
    *,
    event_time: datetime | None,
    data_cutoff: datetime,
    impact_horizon: PolicyImpactHorizon,
) -> float | None:
    if event_time is None:
        return None
    age_hours = (data_cutoff - event_time).total_seconds() / 3600
    if age_hours < 0:
        raise ValueError("future event cannot be scored")
    half_life = {
        PolicyImpactHorizon.INTRADAY: (
            settings.policy_news_freshness_half_life_short_hours
        ),
        PolicyImpactHorizon.SHORT_TERM: (
            settings.policy_news_freshness_half_life_short_hours
        ),
        PolicyImpactHorizon.MEDIUM_TERM: (
            settings.policy_news_freshness_half_life_medium_hours
        ),
        PolicyImpactHorizon.LONG_TERM: (
            settings.policy_news_freshness_half_life_long_hours
        ),
        PolicyImpactHorizon.UNKNOWN: (
            settings.policy_news_freshness_half_life_short_hours
        ),
    }[impact_horizon]
    return math.pow(0.5, age_hours / half_life)


def calculate_policy_news_score(
    *,
    direction: Literal[-1, 0, 1],
    intensity: float | None,
    model_confidence: float | None,
    source_authority_weight: float | None,
    freshness: float | None,
    implementation_weight: float | None,
    verification_weight: float | None,
    relevance_weight: float | None,
    verification_status: PolicyVerificationStatus,
    implementation_status: ImplementationStatus,
) -> PolicyNewsScore:
    components = {
        "intensity": intensity,
        "model_confidence": model_confidence,
        "source_authority_weight": source_authority_weight,
        "freshness_weight": freshness,
        "implementation_weight": implementation_weight,
        "verification_weight": verification_weight,
        "relevance_weight": relevance_weight,
    }
    missing = tuple(
        name for name, value in components.items() if value is None
    )
    zero_for_state = (
        verification_status
        in {
            PolicyVerificationStatus.CONFLICT,
            PolicyVerificationStatus.RETRACTED,
        }
        or (
            direction > 0
            and implementation_status
            in {
                ImplementationStatus.TERMINATED,
                ImplementationStatus.RETRACTED,
            }
        )
    )
    if direction == 0 or missing or zero_for_state:
        score = 0.0
    else:
        score = float(direction)
        for value in components.values():
            score *= float(value)
        limit = settings.policy_news_max_single_event_contribution
        score = max(-limit, min(limit, score))
    known = [
        float(value) for value in components.values() if value is not None
    ]
    confidence = min(known) if known else 0.0
    confidence *= max(0.0, 1.0 - len(missing) * 0.2)
    return PolicyNewsScore(
        score=max(-1.0, min(1.0, score)),
        confidence=max(0.0, min(1.0, confidence)),
        missing_components=missing,
    )


__all__ = [
    "PolicyNewsScore",
    "calculate_policy_news_score",
    "freshness_weight",
]
