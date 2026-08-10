from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from config.settings import settings
from trading.research.capital_flow.aggregator import stable_hash
from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.policy import CAPITAL_FLOW_AGGREGATION_VERSION
from trading.research.capital_flow.schemas import (
    CapitalFlowMarketSnapshot,
    CapitalFlowRiskFlag,
)
from trading.schemas import AnalysisMode


def aggregate_market(
    *,
    bars_by_symbol: dict[str, list[MarketBar]],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    generated_at: datetime | None = None,
) -> CapitalFlowMarketSnapshot:
    latest: dict[str, MarketBar] = {}
    previous: dict[str, MarketBar] = {}
    daily_amounts: dict[object, float] = defaultdict(float)
    daily_known: dict[object, int] = defaultdict(int)
    for symbol, bars in bars_by_symbol.items():
        ordered = sorted(
            [bar for bar in bars if bar.event_time <= data_cutoff and bar.data_cutoff <= data_cutoff],
            key=lambda item: item.event_time,
        )
        if not ordered:
            continue
        latest[symbol] = ordered[-1]
        if len(ordered) >= 2:
            previous[symbol] = ordered[-2]
        for bar in ordered:
            if bar.amount is not None and bar.amount > 0:
                daily_amounts[bar.event_time.date()] += bar.amount
                daily_known[bar.event_time.date()] += 1
    amounts = {
        symbol: bar.amount
        for symbol, bar in latest.items()
        if bar.amount is not None and bar.amount > 0
    }
    total = sum(amounts.values()) if amounts else None
    dates = sorted(daily_amounts)
    current_date = max((bar.event_time.date() for bar in latest.values()), default=None)
    history_dates = [day for day in dates if current_date is not None and day < current_date]
    prior20 = history_dates[-20:]
    average20 = (
        sum(daily_amounts[day] for day in prior20) / 20
        if len(prior20) == 20
        else None
    )
    ratio20 = total / average20 if total is not None and average20 else None
    sorted_amounts = sorted(amounts.values(), reverse=True)
    top10 = (
        max(0.0, min(1.0, sum(sorted_amounts[:10]) / total))
        if total
        else None
    )
    top50 = (
        max(0.0, min(1.0, sum(sorted_amounts[:50]) / total))
        if total
        else None
    )
    rising = falling = 0.0
    high_volume = high_turnover = 0
    evidence_ids: list[str] = []
    for symbol, current in latest.items():
        evidence_ids.append(current.canonical_record_id)
        prior = previous.get(symbol)
        if prior and current.close is not None and prior.close is not None:
            if current.close > prior.close:
                rising += current.amount or 0
            elif current.close < prior.close:
                falling += current.amount or 0
        if prior and current.volume and prior.volume and current.volume / prior.volume >= 2:
            high_volume += 1
        if current.turnover_rate is not None and current.turnover_rate >= settings.capital_flow_high_turnover_threshold:
            high_turnover += 1
    directional = rising + falling
    score = max(-1.0, min(1.0, (rising - falling) / directional)) if directional else 0.0
    sample_size = len(latest)
    partial = sample_size < settings.capital_flow_expected_market_universe_size
    coverage = sample_size / settings.capital_flow_expected_market_universe_size
    confidence = min(
        settings.capital_flow_partial_confidence_cap,
        coverage,
    ) if partial else 1.0
    risks = [CapitalFlowRiskFlag.PARTIAL_UNIVERSE] if partial else []
    missing = []
    if ratio20 is None:
        missing.append("market_amount_ratio_20d")
        risks.append(CapitalFlowRiskFlag.INSUFFICIENT_HISTORY)
    if high_turnover == 0 and not any(bar.turnover_rate is not None for bar in latest.values()):
        missing.append("high_turnover_symbol_count")
    temperature = (
        "HOT"
        if ratio20 is not None and ratio20 >= 1.5
        else "COLD"
        if ratio20 is not None and ratio20 <= 0.6
        else "NEUTRAL"
    )
    actual_generated = generated_at or datetime.now().astimezone()
    if actual_generated < data_cutoff:
        actual_generated = data_cutoff
    input_hash = stable_hash(
        {
            "cutoff": data_cutoff.isoformat(),
            "evidence_ids": sorted(evidence_ids),
            "algorithm": CAPITAL_FLOW_AGGREGATION_VERSION,
        }
    )
    return CapitalFlowMarketSnapshot(
        snapshot_id="cfm_" + input_hash[:32],
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        score=score,
        confidence=confidence,
        market_total_amount=total,
        market_amount_ratio_20d=ratio20,
        market_active_symbol_count=len(amounts),
        high_volume_symbol_count=high_volume,
        high_turnover_symbol_count=high_turnover,
        amount_concentration_top10=top10,
        amount_concentration_top50=top50,
        rising_amount_share=rising / directional if directional else None,
        falling_amount_share=falling / directional if directional else None,
        market_liquidity_temperature=temperature,
        missing_fields=missing,
        risk_flags=sorted(set(risks)),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        input_snapshot_hash=input_hash,
        algorithm_version=CAPITAL_FLOW_AGGREGATION_VERSION,
        shadow_mode=True,
        sample_universe_size=sample_size,
        partial_universe=partial,
        generated_at=actual_generated,
    )


__all__ = ["aggregate_market"]
