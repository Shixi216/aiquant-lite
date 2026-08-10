from __future__ import annotations

from datetime import datetime

from config.settings import settings
from trading.research.capital_flow.aggregator import stable_hash
from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.policy import (
    CAPITAL_FLOW_AGGREGATION_VERSION,
    CAPITAL_FLOW_MAPPING_VERSION,
)
from trading.research.capital_flow.schemas import (
    CapitalFlowRiskFlag,
    CapitalFlowSectorSnapshot,
)
from trading.schemas import AnalysisMode


def aggregate_sector(
    *,
    sector: str,
    bars_by_symbol: dict[str, list[MarketBar]],
    market_total_amount: float | None,
    expected_symbols: set[str],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime | None = None,
    mapping_universe_complete: bool = False,
) -> CapitalFlowSectorSnapshot | None:
    selected = {
        symbol: sorted(bars, key=lambda item: item.event_time)
        for symbol, bars in bars_by_symbol.items()
        if bars and bars[-1].sector == sector
    }
    if not selected:
        return None
    latest = {symbol: bars[-1] for symbol, bars in selected.items()}
    amounts = {
        symbol: bar.amount
        for symbol, bar in latest.items()
        if bar.amount is not None and bar.amount > 0
    }
    total = sum(amounts.values()) if amounts else None
    up = down = 0.0
    for symbol, bars in selected.items():
        if len(bars) < 2:
            continue
        current, previous = bars[-1], bars[-2]
        if current.close is None or previous.close is None:
            continue
        if current.close > previous.close:
            up += current.amount or 0
        elif current.close < previous.close:
            down += current.amount or 0
    directional = up + down
    concentration = (
        max(amounts.values()) / total if total and amounts else None
    )
    mapped = set(selected)
    partial = bool(expected_symbols - mapped) or not mapping_universe_complete
    risk_flags = []
    if partial:
        risk_flags.extend(
            [
                CapitalFlowRiskFlag.PARTIAL_SECTOR,
                CapitalFlowRiskFlag.PARTIAL_UNIVERSE,
            ]
        )
    evidence = [bar.canonical_record_id for bar in latest.values()]
    score = max(-1.0, min(1.0, (up - down) / directional)) if directional else 0.0
    actual_generated = generated_at or datetime.now().astimezone()
    if actual_generated < data_cutoff:
        actual_generated = data_cutoff
    input_hash = stable_hash(
        {
            "sector": sector,
            "cutoff": data_cutoff.isoformat(),
            "evidence": sorted(evidence),
            "mapping": CAPITAL_FLOW_MAPPING_VERSION,
            "mapping_universe_complete": mapping_universe_complete,
        }
    )
    market_share = (
        total / market_total_amount
        if (
            total is not None
            and market_total_amount is not None
            and market_total_amount >= total
            and market_total_amount > 0
        )
        else None
    )
    missing_fields = ["sector_amount_ratio_20d"]
    if market_share is None:
        missing_fields.append("sector_amount_market_share")
    return CapitalFlowSectorSnapshot(
        snapshot_id="cfx_" + input_hash[:32],
        sector=sector,
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        score=score,
        confidence=min(
            (
                1.0
                if not partial
                else settings.capital_flow_partial_confidence_cap
            ),
            len(mapped) / max(len(expected_symbols), 1),
        ),
        sector_total_amount=total,
        sector_amount_market_share=market_share,
        sector_amount_ratio_20d=None,
        sector_up_amount_share=up / directional if directional else None,
        sector_down_amount_share=down / directional if directional else None,
        active_symbol_ratio=len(amounts) / len(mapped) if mapped else None,
        sector_flow_breadth=score,
        sector_concentration=concentration,
        leading_symbols=[
            symbol
            for symbol, _ in sorted(amounts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        mapping_version=CAPITAL_FLOW_MAPPING_VERSION,
        missing_fields=missing_fields,
        risk_flags=risk_flags,
        evidence_ids=evidence,
        input_snapshot_hash=input_hash,
        algorithm_version=CAPITAL_FLOW_AGGREGATION_VERSION,
        shadow_mode=True,
        sample_universe_size=len(mapped),
        partial_universe=True,
        generated_at=actual_generated,
    )


__all__ = ["aggregate_sector"]
