from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from config.settings import settings
from trading.research.sentiment.policy import SENTIMENT_ALGORITHM_VERSION
from trading.research.sentiment.schemas import (
    SentimentVerificationStatus,
)


@dataclass(frozen=True)
class EventScore:
    score: float
    confidence: float
    missing_components: tuple[str, ...]
    algorithm_version: str = SENTIMENT_ALGORITHM_VERSION


def calculate_event_score(
    *,
    direction: Literal[-1, 0, 1],
    intensity: float | None,
    model_confidence: float | None,
    source_quality_weight: float | None,
    freshness_weight: float | None,
    verification_weight: float | None,
    relevance_weight: float | None,
    verification_status: SentimentVerificationStatus,
) -> EventScore:
    components = {
        "intensity": intensity,
        "model_confidence": model_confidence,
        "source_quality_weight": source_quality_weight,
        "freshness_weight": freshness_weight,
        "verification_weight": verification_weight,
        "relevance_weight": relevance_weight,
    }
    missing = tuple(
        key for key, value in components.items() if value is None
    )
    if direction == 0 or verification_status in {
        SentimentVerificationStatus.CONFLICT,
        SentimentVerificationStatus.RETRACTED,
    }:
        score = 0.0
    elif missing:
        score = 0.0
    else:
        raw = float(direction)
        for value in components.values():
            raw *= float(value)
        limit = settings.sentiment_max_single_event_contribution
        score = max(-limit, min(limit, raw))

    known = [
        float(value) for value in components.values() if value is not None
    ]
    confidence = min(known) if known else 0.0
    confidence *= max(0.0, 1.0 - len(missing) * 0.2)
    return EventScore(
        score=max(-1.0, min(1.0, score)),
        confidence=max(0.0, min(1.0, confidence)),
        missing_components=missing,
    )


__all__ = ["EventScore", "calculate_event_score"]
