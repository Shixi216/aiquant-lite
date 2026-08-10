from __future__ import annotations

import hashlib
import json
from datetime import datetime

from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.orchestration.availability import (
    availability_weight,
    directional_score,
)
from trading.research.orchestration.correlation import (
    calculate_correlation_discounts,
    correlation_multipliers,
)
from trading.research.orchestration.models import (
    SHADOW_CONFIGURED_WEIGHTS,
    SHADOW_SCORER_VERSION,
)
from trading.research.orchestration.schemas import (
    FactorAvailabilityStatus,
    FactorBundle,
    OrchestrationRiskFlag,
    ShadowComposite,
)


def stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _risk_flags(
    bundle: FactorBundle,
    *,
    available_count: int,
    confidence: float,
    discount_count: int,
) -> list[OrchestrationRiskFlag]:
    flags = list(bundle.risk_flags)
    if bundle.missing_factor_types:
        flags.append(OrchestrationRiskFlag.FACTOR_DATA_GAP)
    if bundle.factor_coverage.coverage_ratio < 0.60:
        flags.append(OrchestrationRiskFlag.LOW_FACTOR_COVERAGE)
    if available_count == 1:
        flags.append(OrchestrationRiskFlag.SINGLE_FACTOR_DOMINANCE)
    if bundle.stale_factor_types:
        flags.append(OrchestrationRiskFlag.STALE_FACTOR)
    if bundle.conflicting_factor_types:
        flags.append(OrchestrationRiskFlag.FACTOR_CONFLICT)
    if bundle.shared_evidence_groups:
        flags.append(OrchestrationRiskFlag.SHARED_EVIDENCE)
    if discount_count:
        flags.append(OrchestrationRiskFlag.HIGH_FACTOR_CORRELATION)
    if any(
        factor.availability_status
        == FactorAvailabilityStatus.FUTURE_DATA_REJECTED
        for factor in bundle.factor_map().values()
    ):
        flags.append(OrchestrationRiskFlag.FUTURE_FACTOR_REJECTED)
    if any(
        factor.availability_status == FactorAvailabilityStatus.UNVERIFIED
        for factor in bundle.factor_map().values()
    ):
        flags.append(OrchestrationRiskFlag.UNVERIFIED_FACTOR)
    if confidence < 0.35:
        flags.append(OrchestrationRiskFlag.COMPOSITE_CONFIDENCE_LOW)
    flags.append(OrchestrationRiskFlag.FORMAL_WEIGHT_ZERO)
    return list(dict.fromkeys(flags))


def score_shadow_bundle(
    bundle: FactorBundle,
    *,
    generated_at: datetime | None = None,
) -> ShadowComposite:
    factors = bundle.factor_map()
    discounts = calculate_correlation_discounts(factors)
    multipliers = correlation_multipliers(discounts)
    weighted: dict[FactorType, float] = {}
    adjusted_scores: dict[FactorType, float] = {}
    for factor_type, factor in factors.items():
        if factor_type not in SHADOW_CONFIGURED_WEIGHTS:
            continue
        availability = availability_weight(
            factor.availability_status,
            analysis_mode=bundle.analysis_mode,
        )
        if availability <= 0:
            continue
        weighted[factor_type] = (
            SHADOW_CONFIGURED_WEIGHTS[factor_type]
            * factor.confidence
            * availability
            * multipliers.get(factor_type, 1.0)
        )
        adjusted_scores[factor_type] = directional_score(factor)
    denominator = sum(weighted.values())
    if denominator <= 0:
        raise ValueError("no usable factor is available for shadow scoring")
    effective = {
        factor_type: weight / denominator
        for factor_type, weight in weighted.items()
    }
    contributions = {
        factor_type: adjusted_scores[factor_type] * effective[factor_type]
        for factor_type in effective
    }
    score = max(-1.0, min(1.0, sum(contributions.values())))
    available_count = len(effective)
    diversity_penalty = min(1.0, available_count / 3)
    correlation_penalty = 1.0
    for discount in discounts:
        correlation_penalty *= 1 - discount.applied_discount * 0.5
    confidence = max(
        0.0,
        min(
            1.0,
            denominator
            * bundle.factor_coverage.coverage_ratio
            * diversity_penalty
            * correlation_penalty,
        ),
    )
    evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for factor_type in effective
            for evidence_id in factors[factor_type].evidence_ids
        )
    )
    if not evidence_ids:
        raise ValueError("shadow composite requires traceable evidence")
    source_factor_ids = [
        factors[factor_type].factor_output_id for factor_type in effective
    ]
    input_hash = stable_hash(
        {
            "bundle_hash": bundle.input_snapshot_hash,
            "configured_weights": {
                key.value: value
                for key, value in SHADOW_CONFIGURED_WEIGHTS.items()
            },
            "effective_weights": {
                key.value: value for key, value in effective.items()
            },
            "discounts": [
                discount.model_dump(mode="json") for discount in discounts
            ],
            "version": SHADOW_SCORER_VERSION,
        }
    )
    actual_generated = generated_at or datetime.now().astimezone()
    if actual_generated < bundle.data_cutoff:
        actual_generated = bundle.data_cutoff
    risk_flags = _risk_flags(
        bundle,
        available_count=available_count,
        confidence=confidence,
        discount_count=len(discounts),
    )
    metadata = {
        "source_factor_output_ids": source_factor_ids,
        "shared_evidence_groups": [
            group.model_dump(mode="json")
            for group in bundle.shared_evidence_groups
        ],
        "configured_weights": {
            key.value: value for key, value in SHADOW_CONFIGURED_WEIGHTS.items()
        },
        "effective_weights": {
            key.value: value for key, value in effective.items()
        },
        "correlation_discounts": [
            discount.model_dump(mode="json") for discount in discounts
        ],
        "missing_factor_types": [
            item.value for item in bundle.missing_factor_types
        ],
        "stale_factor_types": [
            item.value for item in bundle.stale_factor_types
        ],
        "formal_strategy_weight": 0.0,
        "formula_version": SHADOW_SCORER_VERSION,
        "no_hidden_chain_of_thought": True,
    }
    factor_output = FactorOutput(
        factor_id="fac_" + input_hash[:32],
        symbol=bundle.symbol,
        factor_type=FactorType.SHADOW_COMPOSITE,
        score=score,
        confidence=confidence,
        data_cutoff=bundle.data_cutoff,
        generated_at=actual_generated,
        evidence_ids=evidence_ids,
        risk_flags=[flag.value for flag in risk_flags],
        model_call_ids=[],
        algorithm_version=SHADOW_SCORER_VERSION,
        input_snapshot_hash=input_hash,
        shadow_mode=True,
        metadata=metadata,
    )
    return ShadowComposite(
        factor_output=factor_output,
        score=score,
        confidence=confidence,
        configured_weights={
            key.value: value for key, value in SHADOW_CONFIGURED_WEIGHTS.items()
        },
        effective_weights={
            key.value: value for key, value in effective.items()
        },
        factor_contributions={
            key.value: value for key, value in contributions.items()
        },
        correlation_discounts=discounts,
        source_factor_output_ids=source_factor_ids,
        shared_evidence_groups=bundle.shared_evidence_groups,
        missing_factor_types=bundle.missing_factor_types,
        stale_factor_types=bundle.stale_factor_types,
        risk_flags=risk_flags,
        algorithm_version=SHADOW_SCORER_VERSION,
        input_snapshot_hash=input_hash,
    )


__all__ = ["score_shadow_bundle", "stable_hash"]
