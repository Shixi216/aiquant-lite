from __future__ import annotations

from typing import Any

import pandas as pd

from data_hub.schemas.unified import FactorType
from trading.scanner.models import (
    ResearchStatus,
    ScannerRiskFlag,
)
from trading.scanner.reason_builder import build_reason_codes
from trading.scanner.schemas import (
    ScannerCandidateCard,
    ScannerScoreComponents,
)


_ALL_FACTORS = [
    FactorType.TECHNICAL,
    FactorType.FUNDAMENTAL,
    FactorType.SENTIMENT,
    FactorType.POLICY_NEWS,
    FactorType.CAPITAL_FLOW,
]


def _optional(value: Any) -> Any:
    return None if value is None or pd.isna(value) else value


def _candidate_risks(row: Any) -> list[ScannerRiskFlag]:
    risks = list(row.local_risk_flags)
    if bool(row.snapshot_stale):
        risks.append(ScannerRiskFlag.STALE_SNAPSHOT)
    if row.factor_coverage_count < 2:
        risks.append(ScannerRiskFlag.LOW_FACTOR_COVERAGE)
    if (
        row.composite_confidence is None
        or pd.isna(row.composite_confidence)
        or row.composite_confidence < 0.35
    ):
        risks.append(ScannerRiskFlag.LOW_COMPOSITE_CONFIDENCE)
    if row.missing_filter_fields_internal:
        risks.append(ScannerRiskFlag.MISSING_FILTER_FIELD)
    if not bool(row.current_valid):
        risks.append(ScannerRiskFlag.SCANNER_DATA_GAP)
    risks.append(ScannerRiskFlag.NOT_A_TRADE_RECOMMENDATION)
    return list(dict.fromkeys(risks))


def _factor_order(row: Any, *, positive: bool) -> list[FactorType]:
    values = {
        FactorType.TECHNICAL: _optional(row.technical_score),
        FactorType.FUNDAMENTAL: _optional(row.fundamental_score),
        FactorType.SENTIMENT: _optional(row.sentiment_score),
        FactorType.POLICY_NEWS: _optional(row.policy_news_score),
        FactorType.CAPITAL_FLOW: _optional(row.capital_flow_score),
    }
    selected = [
        (factor, float(score))
        for factor, score in values.items()
        if score is not None and (score > 0 if positive else score < 0)
    ]
    selected.sort(
        key=lambda item: (
            -item[1] if positive else item[1],
            item[0].value,
        )
    )
    return [item[0] for item in selected[:3]]


def build_candidate_cards(
    frame: pd.DataFrame,
    *,
    limit: int,
    candidate_layer: str = "CORE",
    layer_reason: str = "完全命中现有扫描条件",
) -> list[ScannerCandidateCard]:
    cards: list[ScannerCandidateCard] = []
    for index, row in enumerate(
        frame.head(limit).itertuples(index=False),
        start=1,
    ):
        available = [
            FactorType(name) for name in row.available_factor_names
        ]
        missing = [factor for factor in _ALL_FACTORS if factor not in available]
        evidence_ids = (
            list(row.history_bar_ids[-5:])
            if row.history_bar_ids is not None
            and hasattr(row.history_bar_ids, "__len__")
            else []
        )
        risks = _candidate_risks(row)
        missing_fields = list(row.missing_filter_fields_internal)
        for field in (
            "current_price",
            "change_pct",
            "amount",
            "turnover_rate",
        ):
            if _optional(getattr(row, field)) is None:
                missing_fields.append(field)
        status = (
            ResearchStatus.NEEDS_DATA
            if not bool(row.current_valid)
            else ResearchStatus.RESEARCH_CANDIDATE
            if row.factor_coverage_count >= 2
            else ResearchStatus.SCREEN_FLAG_ONLY
        )
        cards.append(
            ScannerCandidateCard(
                rank=index,
                symbol=row.symbol,
                short_name=_optional(row.short_name),
                board=row.board,
                industry=_optional(row.industry),
                snapshot_time=(
                    None
                    if _optional(row.snapshot_time) is None
                    else row.snapshot_time.to_pydatetime()
                ),
                current_price=_optional(row.current_price),
                change_pct=_optional(row.change_pct),
                amount=_optional(row.amount),
                turnover_rate=_optional(row.turnover_rate),
                volume_ratio=_optional(row.volume_ratio_5d),
                anomaly_types=row.anomaly_types,
                scanner_score=row.scanner_score,
                score_components=ScannerScoreComponents.model_validate(
                    row.score_components
                ),
                technical_score=_optional(row.technical_score),
                capital_flow_score=_optional(row.capital_flow_score),
                shadow_composite_score=_optional(row.shadow_composite_score),
                composite_confidence=_optional(row.composite_confidence),
                factor_coverage=f"{row.factor_coverage_count}/5",
                available_factors=available,
                missing_factors=missing,
                missing_fields=list(dict.fromkeys(missing_fields)),
                top_positive_factors=_factor_order(row, positive=True),
                top_negative_factors=_factor_order(row, positive=False),
                reason_codes=build_reason_codes(row),
                risk_flags=risks,
                data_freshness=(
                    "MISSING"
                    if _optional(row.snapshot_time) is None
                    else "STALE"
                    if bool(row.snapshot_stale)
                    else "FRESH"
                ),
                evidence_summary={
                    "snapshot_id": _optional(row.snapshot_id),
                    "evidence_ids": evidence_ids,
                    "anomaly_evidence": row.anomaly_evidence,
                    "history_count": (
                        0
                        if row.history_count is None
                        or pd.isna(row.history_count)
                        else int(row.history_count)
                    ),
                    "no_full_market_evidence_graph": True,
                    "detailed_evidence_built_for_candidate": True,
                },
                research_status=status,
                candidate_layer=candidate_layer,
                layer_reason=layer_reason,
            )
        )
    return cards


__all__ = ["build_candidate_cards"]
