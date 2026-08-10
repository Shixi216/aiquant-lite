from __future__ import annotations

from dataclasses import dataclass

from trading.research.policy_news.models import EventBundle
from trading.research.policy_news.policy import POLICY_NEWS_MAPPING_VERSION
from trading.research.policy_news.schemas import (
    PolicyRiskFlag,
    SectorRelevance,
    SymbolRelevance,
)


@dataclass(frozen=True)
class RelevanceResult:
    symbols: tuple[SymbolRelevance, ...]
    sectors: tuple[SectorRelevance, ...]
    max_weight: float
    risk_flags: tuple[PolicyRiskFlag, ...]


def map_relevance(
    bundle: EventBundle,
    *,
    candidate_symbols: list[str],
    candidate_sectors: list[str],
) -> RelevanceResult:
    evidence_ids = [
        bundle.event_cluster_id,
        *(source.record_id for source in bundle.source_records),
    ]
    symbol_items: list[SymbolRelevance] = []
    for symbol in sorted(set(bundle.symbols)):
        symbol_items.append(
            SymbolRelevance(
                symbol=symbol,
                relevance_weight=1.0,
                relevance_reason=(
                    "事件层存在直接股票链接；未将简称相似或概念标签作为证据"
                ),
                evidence_ids=evidence_ids,
                mapping_version=POLICY_NEWS_MAPPING_VERSION,
            )
        )
    direct_symbols = set(bundle.symbols)
    for symbol in sorted(set(candidate_symbols) - direct_symbols):
        if not symbol:
            continue
        symbol_items.append(
            SymbolRelevance(
                symbol=symbol,
                relevance_weight=0.25,
                relevance_reason=(
                    "仅为结构化提取候选，缺少主营业务或事件层直接链接证据"
                ),
                evidence_ids=evidence_ids,
                mapping_version=POLICY_NEWS_MAPPING_VERSION,
            )
        )

    sector_items: list[SectorRelevance] = []
    for sector in sorted(set(bundle.sectors)):
        sector_items.append(
            SectorRelevance(
                sector=sector,
                relevance_weight=1.0,
                relevance_reason="事件层存在直接行业链接",
                evidence_ids=evidence_ids,
                mapping_version=POLICY_NEWS_MAPPING_VERSION,
            )
        )
    # Model candidates are not promoted to sector links without local mapping
    # evidence. Keeping them out prevents policy-wide concept-stock expansion.
    _ = candidate_sectors

    flags: list[PolicyRiskFlag] = []
    if any(item.relevance_weight < 0.75 for item in symbol_items):
        flags.append(PolicyRiskFlag.SYMBOL_RELEVANCE_LOW)
    if not sector_items:
        flags.append(PolicyRiskFlag.SECTOR_MAPPING_MISSING)
    return RelevanceResult(
        symbols=tuple(symbol_items),
        sectors=tuple(sector_items),
        max_weight=max(
            (
                item.relevance_weight
                for item in [*symbol_items, *sector_items]
            ),
            default=0.0,
        ),
        risk_flags=tuple(flags),
    )


__all__ = ["RelevanceResult", "map_relevance"]
