from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Literal

from trading.research.policy_news.policy import (
    POLICY_NEWS_AGGREGATION_VERSION,
)
from trading.research.policy_news.schemas import (
    ImplementationStatus,
    PolicyEventCategory,
    PolicyNewsEventAnalysis,
    PolicyNewsSectorSnapshot,
    PolicyNewsSymbolSnapshot,
    PolicyRiskFlag,
    PolicyVerificationStatus,
)
from trading.schemas import AnalysisMode


def _stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _aggregate(
    *,
    target: str,
    target_kind: Literal["symbol", "sector"],
    analyses: list[PolicyNewsEventAnalysis],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime,
) -> dict[str, object]:
    unique = {
        item.event_cluster_id: item
        for item in sorted(
            analyses,
            key=lambda analysis: (
                analysis.data_cutoff,
                analysis.generated_at,
            ),
        )
        if item.event_category
        not in {
            PolicyEventCategory.OTHER,
            PolicyEventCategory.NOT_APPLICABLE,
        }
    }
    relevant: list[tuple[PolicyNewsEventAnalysis, float]] = []
    for item in unique.values():
        mappings = (
            item.symbol_relevance
            if target_kind == "symbol"
            else item.sector_relevance
        )
        key = "symbol" if target_kind == "symbol" else "sector"
        weight = next(
            (
                mapping.relevance_weight
                for mapping in mappings
                if getattr(mapping, key) == target
            ),
            0.0,
        )
        if weight > 0:
            relevant.append((item, weight))
    contributions = []
    for item, target_weight in relevant:
        base_relevance = item.relevance_weight
        contribution = (
            item.policy_news_score * target_weight / base_relevance
            if base_relevance > 0
            else 0.0
        )
        contributions.append(contribution)
    score = (
        sum(contributions) / math.sqrt(len(contributions))
        if contributions
        else 0.0
    )
    score = max(-1.0, min(1.0, score))
    positive = sorted(
        (
            (contribution, item.event_cluster_id)
            for (item, _), contribution in zip(
                relevant,
                contributions,
                strict=True,
            )
            if contribution > 0
        ),
        reverse=True,
    )
    negative = sorted(
        (
            (contribution, item.event_cluster_id)
            for (item, _), contribution in zip(
                relevant,
                contributions,
                strict=True,
            )
            if contribution < 0
        )
    )
    flags = {
        flag for item, _ in relevant for flag in item.risk_flags
    }
    if not relevant:
        flags.add(PolicyRiskFlag.DATA_GAP)
    if target_kind == "sector" and not relevant:
        flags.add(PolicyRiskFlag.SECTOR_MAPPING_MISSING)
    evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for item, _ in relevant
            for evidence_id in item.evidence_ids
        )
    )
    model_call_ids = list(
        dict.fromkeys(
            call_id
            for item, _ in relevant
            for call_id in item.model_call_ids
        )
    )
    shared_event_ids = sorted(
        {
            item.event_cluster_id
            for item, _ in relevant
            if item.shared_sentiment_analysis_ids
        }
    )
    horizon_scores: dict[str, float] = {}
    for horizon in sorted(
        {item.impact_horizon.value for item, _ in relevant}
    ):
        values = [
            contribution
            for (item, _), contribution in zip(
                relevant,
                contributions,
                strict=True,
            )
            if item.impact_horizon.value == horizon
        ]
        horizon_scores[horizon] = max(
            -1.0,
            min(
                1.0,
                sum(values) / math.sqrt(len(values)) if values else 0.0,
            ),
        )
    confidence = (
        sum(item.confidence for item, _ in relevant) / len(relevant)
        if relevant
        else 0.0
    )
    payload = {
        "target": target,
        "target_kind": target_kind,
        "analysis_mode": analysis_mode.value,
        "data_cutoff": data_cutoff.isoformat(),
        "analysis_ids": sorted(
            item.policy_analysis_id for item, _ in relevant
        ),
        "contributions": contributions,
        "version": POLICY_NEWS_AGGREGATION_VERSION,
    }
    input_hash = _stable_hash(payload)
    return {
        "snapshot_id": (
            ("pns_" if target_kind == "symbol" else "pnx_")
            + input_hash[:32]
        ),
        target_kind: target,
        "analysis_mode": analysis_mode,
        "data_cutoff": data_cutoff,
        "generated_at": generated_at,
        "event_count": len(relevant),
        "positive_event_count": sum(value > 0 for value in contributions),
        "negative_event_count": sum(value < 0 for value in contributions),
        "neutral_event_count": sum(value == 0 for value in contributions),
        "weighted_policy_score": score,
        "high_authority_event_count": sum(
            item.source_authority >= 0.9 for item, _ in relevant
        ),
        "implemented_event_count": sum(
            item.implementation_status
            in {
                ImplementationStatus.IMPLEMENTING,
                ImplementationStatus.EXECUTED,
            }
            for item, _ in relevant
        ),
        "conflict_event_count": sum(
            item.verification_status == PolicyVerificationStatus.CONFLICT
            for item, _ in relevant
        ),
        "confidence": max(0.0, min(1.0, confidence)),
        "horizon_scores": horizon_scores,
        "top_positive_events": [item[1] for item in positive[:5]],
        "top_negative_events": [item[1] for item in negative[:5]],
        "evidence_ids": evidence_ids,
        "model_call_ids": model_call_ids,
        "shared_sentiment_event_ids": shared_event_ids,
        "missing_fields": (
            []
            if relevant
            else [
                (
                    "policy_news_events"
                    if target_kind == "symbol"
                    else "policy_news_sector_events"
                )
            ]
        ),
        "risk_flags": sorted(flags, key=lambda flag: flag.value),
        "input_snapshot_hash": input_hash,
        "algorithm_version": POLICY_NEWS_AGGREGATION_VERSION,
    }


def aggregate_symbol(
    *,
    symbol: str,
    analyses: list[PolicyNewsEventAnalysis],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime,
) -> PolicyNewsSymbolSnapshot:
    return PolicyNewsSymbolSnapshot.model_validate(
        _aggregate(
            target=symbol,
            target_kind="symbol",
            analyses=analyses,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
            generated_at=generated_at,
        )
    )


def aggregate_sector(
    *,
    sector: str,
    analyses: list[PolicyNewsEventAnalysis],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime,
) -> PolicyNewsSectorSnapshot:
    return PolicyNewsSectorSnapshot.model_validate(
        _aggregate(
            target=sector,
            target_kind="sector",
            analyses=analyses,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
            generated_at=generated_at,
        )
    )


__all__ = ["aggregate_sector", "aggregate_symbol"]
