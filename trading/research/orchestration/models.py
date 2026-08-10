from __future__ import annotations

from dataclasses import dataclass

from data_hub.schemas.unified import FactorType


ORCHESTRATION_ALGORITHM_VERSION = "five-factor-orchestration-v1"
EVIDENCE_GRAPH_VERSION = "five-factor-evidence-graph-v1"
CORRELATION_ALGORITHM_VERSION = "five-factor-correlation-v1"
SHADOW_SCORER_VERSION = "shadow-composite-v1"
FORMAL_STRATEGY_VERSION = "auditable_multi_factor_v1"

BASE_FACTOR_TYPES: tuple[FactorType, ...] = (
    FactorType.TECHNICAL,
    FactorType.FUNDAMENTAL,
    FactorType.SENTIMENT,
    FactorType.POLICY_NEWS,
    FactorType.CAPITAL_FLOW,
)

SHADOW_CONFIGURED_WEIGHTS: dict[FactorType, float] = {
    FactorType.TECHNICAL: 0.30,
    FactorType.FUNDAMENTAL: 0.25,
    FactorType.SENTIMENT: 0.15,
    FactorType.POLICY_NEWS: 0.15,
    FactorType.CAPITAL_FLOW: 0.15,
}

FORMAL_WEIGHTS: dict[FactorType, float] = {
    FactorType.TECHNICAL: 0.60,
    FactorType.FUNDAMENTAL: 0.40,
}


@dataclass(frozen=True)
class CorrelationRule:
    left: FactorType
    right: FactorType
    discounted_factor: FactorType
    maximum_discount: float
    overlap_basis: str


CORRELATION_RULES: tuple[CorrelationRule, ...] = (
    CorrelationRule(
        left=FactorType.SENTIMENT,
        right=FactorType.POLICY_NEWS,
        discounted_factor=FactorType.POLICY_NEWS,
        maximum_discount=0.50,
        overlap_basis="event_cluster_jaccard",
    ),
    CorrelationRule(
        left=FactorType.TECHNICAL,
        right=FactorType.CAPITAL_FLOW,
        discounted_factor=FactorType.CAPITAL_FLOW,
        maximum_discount=0.20,
        overlap_basis="shared_market_record_ratio",
    ),
)

AVAILABILITY_WEIGHTS: dict[str, float] = {
    "AVAILABLE": 1.0,
    "PARTIAL": 0.65,
    "MISSING": 0.0,
    "STALE": 0.25,
    "CONFLICT": 0.0,
    "MODE_RESTRICTED": 0.0,
    "FUTURE_DATA_REJECTED": 0.0,
    "UNVERIFIED": 0.40,
}

DECISION_EXCLUDED_AVAILABILITY = frozenset(
    {
        "MISSING",
        "STALE",
        "CONFLICT",
        "MODE_RESTRICTED",
        "FUTURE_DATA_REJECTED",
    }
)


__all__ = [
    "AVAILABILITY_WEIGHTS",
    "BASE_FACTOR_TYPES",
    "CORRELATION_ALGORITHM_VERSION",
    "CORRELATION_RULES",
    "DECISION_EXCLUDED_AVAILABILITY",
    "EVIDENCE_GRAPH_VERSION",
    "FORMAL_STRATEGY_VERSION",
    "FORMAL_WEIGHTS",
    "ORCHESTRATION_ALGORITHM_VERSION",
    "SHADOW_CONFIGURED_WEIGHTS",
    "SHADOW_SCORER_VERSION",
    "CorrelationRule",
]
