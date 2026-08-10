from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from trading.schemas import AnalysisMode


POLICY_NEWS_EXTRACTOR_VERSION = "policy-news-extractor-v1"
POLICY_NEWS_SCORER_VERSION = "policy-news-local-score-v1"
POLICY_NEWS_AGGREGATION_VERSION = "policy-news-aggregate-v1"
POLICY_NEWS_PROMPT_VERSION = "policy-news-json-v1"
POLICY_NEWS_MAPPING_VERSION = "policy-news-evidence-mapping-v1"
POLICY_NEWS_SOURCE_VERSION = "policy-news-source-authority-v1"
POLICY_NEWS_IMPLEMENTATION_VERSION = "policy-news-implementation-v1"
POLICY_NEWS_EVALUATION_VERSION = "policy-news-evaluation-v1"
POLICY_NEWS_TEXT_VERSION = "policy-news-text-completeness-v1"


@dataclass(frozen=True)
class PolicyNewsModePolicy:
    allow_model_calls: bool
    allow_external_fetch: bool
    persist_analysis: bool
    persist_factor: bool
    strict_point_in_time: bool
    use_existing_snapshot: bool
    shadow_mode: bool = True


_MODE_POLICIES = {
    AnalysisMode.SCREENING: PolicyNewsModePolicy(
        allow_model_calls=False,
        allow_external_fetch=False,
        persist_analysis=False,
        persist_factor=False,
        strict_point_in_time=True,
        use_existing_snapshot=True,
    ),
    AnalysisMode.RESEARCH: PolicyNewsModePolicy(
        allow_model_calls=True,
        allow_external_fetch=True,
        persist_analysis=True,
        persist_factor=True,
        strict_point_in_time=True,
        use_existing_snapshot=False,
    ),
    AnalysisMode.DECISION: PolicyNewsModePolicy(
        allow_model_calls=False,
        allow_external_fetch=False,
        persist_analysis=True,
        persist_factor=True,
        strict_point_in_time=True,
        use_existing_snapshot=False,
    ),
}


def policy_for(mode: AnalysisMode) -> PolicyNewsModePolicy:
    return _MODE_POLICIES[mode]


def source_authority_weights() -> dict[str, float]:
    return {
        "central_government": (
            settings.policy_news_source_central_government_weight
        ),
        "regulator": settings.policy_news_source_regulator_weight,
        "local_government": (
            settings.policy_news_source_local_government_weight
        ),
        "company_announcement": (
            settings.policy_news_source_company_announcement_weight
        ),
        "authoritative_media": (
            settings.policy_news_source_authoritative_media_weight
        ),
        "media": settings.policy_news_source_media_weight,
        "analyst": settings.policy_news_source_analyst_weight,
        "unknown": settings.policy_news_source_unknown_weight,
        "rumor": settings.policy_news_source_rumor_weight,
    }


def implementation_weights() -> dict[str, float]:
    return {
        "RUMOR": settings.policy_news_implementation_rumor_weight,
        "DRAFT": settings.policy_news_implementation_draft_weight,
        "CONSULTATION": (
            settings.policy_news_implementation_consultation_weight
        ),
        "ANNOUNCED": settings.policy_news_implementation_announced_weight,
        "APPROVED": settings.policy_news_implementation_approved_weight,
        "IMPLEMENTING": (
            settings.policy_news_implementation_implementing_weight
        ),
        "EXECUTED": settings.policy_news_implementation_executed_weight,
        "SUSPENDED": settings.policy_news_implementation_suspended_weight,
        "TERMINATED": (
            settings.policy_news_implementation_terminated_weight
        ),
        "RETRACTED": settings.policy_news_implementation_retracted_weight,
        "UNKNOWN": settings.policy_news_implementation_unknown_weight,
    }


def verification_weights() -> dict[str, float]:
    return {
        "VERIFIED_OFFICIAL": (
            settings.policy_news_verification_official_weight
        ),
        "VERIFIED_MULTI_SOURCE": (
            settings.policy_news_verification_multi_source_weight
        ),
        "SINGLE_OFFICIAL_SOURCE": (
            settings.policy_news_verification_single_official_weight
        ),
        "SINGLE_MEDIA_SOURCE": (
            settings.policy_news_verification_single_media_weight
        ),
        "UNVERIFIED": settings.policy_news_verification_unverified_weight,
        "CONFLICT": settings.policy_news_verification_conflict_weight,
        "RETRACTED": 0.0,
    }


__all__ = [
    "POLICY_NEWS_AGGREGATION_VERSION",
    "POLICY_NEWS_EVALUATION_VERSION",
    "POLICY_NEWS_EXTRACTOR_VERSION",
    "POLICY_NEWS_IMPLEMENTATION_VERSION",
    "POLICY_NEWS_MAPPING_VERSION",
    "POLICY_NEWS_PROMPT_VERSION",
    "POLICY_NEWS_SCORER_VERSION",
    "POLICY_NEWS_SOURCE_VERSION",
    "POLICY_NEWS_TEXT_VERSION",
    "PolicyNewsModePolicy",
    "implementation_weights",
    "policy_for",
    "source_authority_weights",
    "verification_weights",
]
