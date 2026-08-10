from __future__ import annotations

from enum import StrEnum


class CandidateLayer(StrEnum):
    CORE = "CORE"
    NEAR = "NEAR"
    CONTROL = "CONTROL"


def classify_daily_candidate(
    *,
    price: float,
    sma20: float,
    sma60: float,
    technical_score: float,
) -> CandidateLayer:
    """Classify with the frozen production scan conditions."""

    moving_average_hit = price > sma20 > sma60
    score_hit = technical_score > 0.3
    if moving_average_hit and score_hit:
        return CandidateLayer.CORE
    if moving_average_hit or score_hit:
        return CandidateLayer.NEAR
    return CandidateLayer.CONTROL


__all__ = ["CandidateLayer", "classify_daily_candidate"]