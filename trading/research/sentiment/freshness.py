from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config.settings import settings
from trading.research.sentiment.policy import FRESHNESS_VERSION
from trading.research.sentiment.schemas import (
    ImpactHorizon,
    SentimentRiskFlag,
)


@dataclass(frozen=True)
class FreshnessResult:
    weight: float
    age_hours: float | None
    risk_flags: tuple[SentimentRiskFlag, ...]
    algorithm_version: str = FRESHNESS_VERSION


def _half_life(horizon: ImpactHorizon) -> float:
    return {
        ImpactHorizon.INTRADAY: (
            settings.sentiment_half_life_intraday_hours
        ),
        ImpactHorizon.ONE_DAY: settings.sentiment_half_life_1d_hours,
        ImpactHorizon.ONE_TO_FIVE_DAYS: (
            settings.sentiment_half_life_1_5d_hours
        ),
        ImpactHorizon.ONE_TO_FOUR_WEEKS: (
            settings.sentiment_half_life_1_4w_hours
        ),
        ImpactHorizon.LONG_TERM: (
            settings.sentiment_half_life_long_term_hours
        ),
        ImpactHorizon.UNKNOWN: settings.sentiment_half_life_unknown_hours,
    }[horizon]


def freshness_weight(
    *,
    event_time: datetime | None,
    data_cutoff: datetime,
    impact_horizon: ImpactHorizon,
) -> FreshnessResult:
    if data_cutoff.tzinfo is None or data_cutoff.utcoffset() is None:
        raise ValueError("data_cutoff must include a timezone")
    if event_time is None:
        return FreshnessResult(
            weight=0.0,
            age_hours=None,
            risk_flags=(SentimentRiskFlag.EVENT_TIME_MISSING,),
        )
    if event_time.tzinfo is None or event_time.utcoffset() is None:
        raise ValueError("event_time must include a timezone")
    if event_time > data_cutoff:
        raise ValueError("future events must not enter sentiment analysis")
    age_hours = max(
        0.0,
        (data_cutoff - event_time).total_seconds() / 3600,
    )
    weight = 2 ** (-age_hours / _half_life(impact_horizon))
    flags: tuple[SentimentRiskFlag, ...] = ()
    if weight < 0.1:
        flags = (SentimentRiskFlag.STALE_SENTIMENT,)
    return FreshnessResult(
        weight=max(0.0, min(1.0, weight)),
        age_hours=age_hours,
        risk_flags=flags,
    )


__all__ = ["FreshnessResult", "freshness_weight"]
