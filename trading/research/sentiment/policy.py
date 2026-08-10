from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from trading.schemas import AnalysisMode


SENTIMENT_ALGORITHM_VERSION = "sentiment-local-score-v1"
SENTIMENT_AGGREGATION_VERSION = "sentiment-symbol-aggregate-v1"
MARKET_BREADTH_VERSION = "market-breadth-v1"
SENTIMENT_EVALUATION_VERSION = "sentiment-evaluation-v1"
SENTIMENT_EXTRACTOR_VERSION = "sentiment-extractor-v1"
SENTIMENT_PROMPT_VERSION = "sentiment-json-v1"
SOURCE_QUALITY_VERSION = "sentiment-source-quality-v1"
VERIFICATION_VERSION = "sentiment-verification-v1"
FRESHNESS_VERSION = "sentiment-freshness-v1"


@dataclass(frozen=True)
class SentimentModePolicy:
    allow_model_calls: bool
    allow_external_fetch: bool
    persist_analysis: bool
    persist_factor: bool
    strict_point_in_time: bool
    use_existing_snapshot: bool
    shadow_mode: bool = True


_MODE_POLICIES = {
    AnalysisMode.SCREENING: SentimentModePolicy(
        allow_model_calls=False,
        allow_external_fetch=False,
        persist_analysis=False,
        persist_factor=False,
        strict_point_in_time=True,
        use_existing_snapshot=True,
    ),
    AnalysisMode.RESEARCH: SentimentModePolicy(
        allow_model_calls=True,
        allow_external_fetch=True,
        persist_analysis=True,
        persist_factor=True,
        strict_point_in_time=False,
        use_existing_snapshot=False,
    ),
    AnalysisMode.DECISION: SentimentModePolicy(
        allow_model_calls=True,
        allow_external_fetch=False,
        persist_analysis=True,
        persist_factor=True,
        strict_point_in_time=True,
        use_existing_snapshot=False,
    ),
}


def policy_for(mode: AnalysisMode) -> SentimentModePolicy:
    return _MODE_POLICIES[mode]


def source_quality_weights() -> dict[str, float]:
    return {
        "official": settings.sentiment_source_official_weight,
        "authoritative_media": (
            settings.sentiment_source_authoritative_media_weight
        ),
        "media": settings.sentiment_source_media_weight,
        "analyst": settings.sentiment_source_analyst_weight,
        "unknown": settings.sentiment_source_unknown_weight,
        "rumor": settings.sentiment_source_rumor_weight,
    }


def verification_weights() -> dict[str, float]:
    return {
        "VERIFIED_OFFICIAL": (
            settings.sentiment_verification_official_weight
        ),
        "VERIFIED_MULTI_SOURCE": (
            settings.sentiment_verification_multi_source_weight
        ),
        "SINGLE_OFFICIAL_SOURCE": (
            settings.sentiment_verification_single_official_weight
        ),
        "SINGLE_MEDIA_SOURCE": (
            settings.sentiment_verification_single_media_weight
        ),
        "CONFLICT": settings.sentiment_verification_conflict_weight,
        "UNVERIFIED": settings.sentiment_verification_unverified_weight,
        "RETRACTED": settings.sentiment_verification_retracted_weight,
    }


__all__ = [
    "FRESHNESS_VERSION",
    "MARKET_BREADTH_VERSION",
    "SENTIMENT_AGGREGATION_VERSION",
    "SENTIMENT_ALGORITHM_VERSION",
    "SENTIMENT_EVALUATION_VERSION",
    "SENTIMENT_EXTRACTOR_VERSION",
    "SENTIMENT_PROMPT_VERSION",
    "SOURCE_QUALITY_VERSION",
    "VERIFICATION_VERSION",
    "SentimentModePolicy",
    "policy_for",
    "source_quality_weights",
    "verification_weights",
]
