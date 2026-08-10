from __future__ import annotations

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.evidence_graph import (
    event_cluster_ids,
    market_record_ids,
)
from trading.research.orchestration.models import (
    CORRELATION_ALGORITHM_VERSION,
    CORRELATION_RULES,
)
from trading.research.orchestration.schemas import (
    CorrelationDiscount,
    FactorView,
)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _smaller_set_overlap(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    return len(left & right) / denominator if denominator else 0.0


def calculate_correlation_discounts(
    factors: dict[FactorType, FactorView],
) -> list[CorrelationDiscount]:
    discounts: list[CorrelationDiscount] = []
    for rule in CORRELATION_RULES:
        left = factors.get(rule.left)
        right = factors.get(rule.right)
        if left is None or right is None:
            continue
        shared_clusters: list[str] = []
        if rule.overlap_basis == "event_cluster_jaccard":
            left_ids = event_cluster_ids(left)
            right_ids = event_cluster_ids(right)
            shared_clusters = sorted(left_ids & right_ids)
            shared_ids = shared_clusters
            ratio = _jaccard(left_ids, right_ids)
        else:
            left_ids = market_record_ids(left)
            right_ids = market_record_ids(right)
            shared_ids = sorted(left_ids & right_ids)
            ratio = _smaller_set_overlap(left_ids, right_ids)
        if ratio <= 0:
            continue
        applied = min(rule.maximum_discount, rule.maximum_discount * ratio)
        discounts.append(
            CorrelationDiscount(
                factor_pair=f"{rule.left.value}<->{rule.right.value}",
                overlap_ratio=ratio,
                applied_discount=applied,
                discounted_factor=rule.discounted_factor,
                shared_evidence_ids=shared_ids,
                shared_event_cluster_ids=shared_clusters,
                source_factor_output_ids=[
                    left.factor_output_id,
                    right.factor_output_id,
                ],
                algorithm_version=CORRELATION_ALGORITHM_VERSION,
                explanation=(
                    f"{rule.discounted_factor.value} contribution is reduced "
                    f"by {applied:.4f}; overlap basis={rule.overlap_basis}"
                ),
            )
        )
    return discounts


def correlation_multipliers(
    discounts: list[CorrelationDiscount],
) -> dict[FactorType, float]:
    multipliers: dict[FactorType, float] = {}
    for discount in discounts:
        current = multipliers.get(discount.discounted_factor, 1.0)
        multipliers[discount.discounted_factor] = current * (
            1 - discount.applied_discount
        )
    return multipliers


__all__ = [
    "calculate_correlation_discounts",
    "correlation_multipliers",
]
