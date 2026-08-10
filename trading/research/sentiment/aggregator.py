from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

from trading.research.sentiment.policy import (
    SENTIMENT_AGGREGATION_VERSION,
)
from trading.research.sentiment.schemas import (
    SentimentEventAnalysis,
    SentimentEventType,
    SentimentRiskFlag,
    SentimentSymbolSnapshot,
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


def aggregate_symbol(
    *,
    symbol: str,
    analyses: list[SentimentEventAnalysis],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime,
) -> SentimentSymbolSnapshot:
    unique = {
        analysis.event_cluster_id: analysis
        for analysis in sorted(
            analyses,
            key=lambda item: (item.data_cutoff, item.generated_at),
        )
        if symbol in analysis.affected_symbols
    }
    relevant = list(unique.values())
    contributions = [
        analysis.event_score
        * analysis.relevance_by_symbol.get(symbol, 0.0)
        for analysis in relevant
    ]
    score = (
        sum(contributions) / math.sqrt(len(contributions))
        if contributions
        else 0.0
    )
    score = max(-1.0, min(1.0, score))
    positive = sorted(
        (
            (analysis.event_score, analysis.event_cluster_id)
            for analysis in relevant
            if analysis.event_score > 0
        ),
        reverse=True,
    )
    negative = sorted(
        (
            (analysis.event_score, analysis.event_cluster_id)
            for analysis in relevant
            if analysis.event_score < 0
        )
    )
    rumor_count = sum(
        analysis.event_type == SentimentEventType.MARKET_RUMOR
        for analysis in relevant
    )
    confidence = (
        sum(item.confidence for item in relevant) / len(relevant)
        if relevant
        else 0.0
    )
    flags = {
        flag for analysis in relevant for flag in analysis.risk_flags
    }
    if not relevant:
        flags.add(SentimentRiskFlag.DATA_GAP)
    if relevant and rumor_count / len(relevant) >= 0.5:
        flags.add(SentimentRiskFlag.HIGH_RUMOR_RATIO)
        confidence *= 0.5
    evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for analysis in relevant
            for evidence_id in analysis.evidence_ids
        )
    )
    model_call_ids = list(
        dict.fromkeys(
            call_id
            for analysis in relevant
            for call_id in analysis.model_call_ids
        )
    )
    missing_fields = [] if relevant else ["sentiment_events"]
    payload = {
        "symbol": symbol,
        "analysis_mode": analysis_mode.value,
        "data_cutoff": data_cutoff.isoformat(),
        "event_analysis_ids": sorted(
            item.sentiment_analysis_id for item in relevant
        ),
        "contributions": contributions,
        "algorithm_version": SENTIMENT_AGGREGATION_VERSION,
    }
    input_hash = _stable_hash(payload)
    return SentimentSymbolSnapshot(
        snapshot_id="ssn_" + input_hash[:32],
        symbol=symbol,
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        generated_at=generated_at,
        event_count=len(relevant),
        positive_event_count=sum(item.event_score > 0 for item in relevant),
        negative_event_count=sum(item.event_score < 0 for item in relevant),
        neutral_event_count=sum(item.event_score == 0 for item in relevant),
        weighted_event_score=score,
        propagation_heat=sum(item.propagation_heat for item in relevant),
        confidence=max(0.0, min(1.0, confidence)),
        top_positive_events=[item[1] for item in positive[:5]],
        top_negative_events=[item[1] for item in negative[:5]],
        evidence_ids=evidence_ids,
        model_call_ids=model_call_ids,
        missing_fields=missing_fields,
        risk_flags=sorted(flags, key=lambda flag: flag.value),
        input_snapshot_hash=input_hash,
        algorithm_version=SENTIMENT_AGGREGATION_VERSION,
    )


__all__ = ["aggregate_symbol"]
