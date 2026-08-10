from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from config.settings import settings
from trading.research.capital_flow.amount_features import calculate_amount_features
from trading.research.capital_flow.financing_features import calculate_financing_features
from trading.research.capital_flow.models import EstimatedFlow, FinancingRecord, MarketBar
from trading.research.capital_flow.policy import CAPITAL_FLOW_ALGORITHM_VERSION
from trading.research.capital_flow.price_volume import classify_price_volume
from trading.research.capital_flow.scorer import (
    combine_sub_scores,
    financing_score,
    liquidity_score,
    price_volume_score,
    ratio_score,
    turnover_score,
)
from trading.research.capital_flow.schemas import (
    CapitalFlowRiskFlag,
    CapitalFlowSymbolSnapshot,
    LiquidityLevel,
    SubScore,
)
from trading.research.capital_flow.turnover_features import calculate_turnover_features
from trading.research.capital_flow.volume_features import calculate_volume_features
from trading.schemas import AnalysisMode


def stable_hash(value: Any) -> str:
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


def _liquidity(
    bars: list[MarketBar],
    average_amount_20d: float | None,
) -> tuple[LiquidityLevel, float, int, bool, tuple[CapitalFlowRiskFlag, ...]]:
    recent = sorted(bars, key=lambda item: item.event_time)[-20:]
    zero_days = sum(
        (bar.volume is not None and bar.volume <= 0)
        or (bar.amount is not None and bar.amount <= 0)
        for bar in recent
    )
    current = recent[-1] if recent else None
    suspended = bool(
        current
        and (
            current.volume is not None
            and current.volume <= 0
            or current.amount is not None
            and current.amount <= 0
        )
    )
    flags: set[CapitalFlowRiskFlag] = set()
    if suspended:
        level = LiquidityLevel.ILLIQUID
        confidence = 1.0
        flags.add(CapitalFlowRiskFlag.SUSPENDED_OR_ILLIQUID)
    elif average_amount_20d is None:
        level = LiquidityLevel.UNKNOWN
        confidence = 0.0
    elif average_amount_20d < settings.capital_flow_low_liquidity_amount:
        level = LiquidityLevel.LOW
        confidence = 0.8
        flags.add(CapitalFlowRiskFlag.LOW_LIQUIDITY)
    elif average_amount_20d >= settings.capital_flow_high_liquidity_amount:
        level = LiquidityLevel.HIGH
        confidence = 0.8
    else:
        level = LiquidityLevel.MEDIUM
        confidence = 0.8
    return level, confidence, zero_days, suspended, tuple(sorted(flags))


def aggregate_symbol(
    *,
    symbol: str,
    bars: list[MarketBar],
    analysis_mode: AnalysisMode,
    data_cutoff: datetime,
    market_amounts: dict[str, float],
    universe_complete: bool,
    amount_market_ranks: dict[str, int] | None = None,
    amount_market_percentiles: dict[str, float] | None = None,
    financing_records: list[FinancingRecord] | None = None,
    sector_score: SubScore | None = None,
    estimated_flow: EstimatedFlow | None = None,
    generated_at: datetime | None = None,
) -> CapitalFlowSymbolSnapshot:
    ordered = sorted(
        [bar for bar in bars if bar.event_time <= data_cutoff and bar.data_cutoff <= data_cutoff],
        key=lambda item: item.event_time,
    )
    if not ordered:
        raise ValueError(f"No point-in-time market bars for {symbol}")
    volume = calculate_volume_features(ordered)
    amount = calculate_amount_features(ordered)
    turnover = calculate_turnover_features(
        ordered,
        high_threshold=settings.capital_flow_high_turnover_threshold,
    )
    pv_state, pv_flags = classify_price_volume(
        ordered,
        price_change_threshold=settings.capital_flow_price_change_threshold,
        volume_change_threshold=settings.capital_flow_volume_change_threshold,
    )
    financing = calculate_financing_features(financing_records or [])
    level, liquidity_confidence, zero_days, suspended, liquidity_flags = _liquidity(
        ordered,
        amount.average_amount_20d,
    )
    if amount_market_ranks is None or amount_market_percentiles is None:
        ranked = sorted(
            market_amounts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        rank_map = {
            item[0]: index
            for index, item in enumerate(ranked, 1)
        }
        percentile_map = {
            item[0]: 1 - (index - 1) / len(ranked)
            for index, item in enumerate(ranked, 1)
        }
    else:
        rank_map = amount_market_ranks
        percentile_map = amount_market_percentiles
    rank = rank_map.get(symbol)
    market_percentile = percentile_map.get(symbol)
    sub_scores = {
        "volume": ratio_score(volume.volume_ratio_20d or volume.volume_ratio_5d, 0.75),
        "amount": ratio_score(amount.amount_ratio_20d or amount.amount_ratio_5d, 0.75),
        "turnover": turnover_score(
            turnover.turnover_percentile_20d,
            turnover.turnover_rate,
        ),
        "price_volume": price_volume_score(pv_state),
        "financing": financing_score(financing.trend),
        "sector_flow": sector_score or SubScore(value=None, confidence=0, available=False),
        "liquidity": liquidity_score(level, liquidity_confidence),
    }
    combined = combine_sub_scores(sub_scores)
    risks = {
        *volume.risk_flags,
        *amount.risk_flags,
        *turnover.risk_flags,
        *pv_flags,
        *financing.risk_flags,
        *liquidity_flags,
        *(CapitalFlowRiskFlag(flag) for bar in ordered for flag in bar.unit_risk_flags),
    }
    if not universe_complete:
        risks.add(CapitalFlowRiskFlag.PARTIAL_UNIVERSE)
    if estimated_flow is not None:
        risks.add(CapitalFlowRiskFlag.ESTIMATED_FLOW_ONLY)
    if (data_cutoff.date() - ordered[-1].event_time.date()).days > settings.capital_flow_stale_market_days:
        risks.add(CapitalFlowRiskFlag.STALE_MARKET_DATA)
    missing: set[str] = set()
    fields = {
        "volume_ratio_20d": volume.volume_ratio_20d,
        "amount_ratio_20d": amount.amount_ratio_20d,
        "turnover_rate": turnover.turnover_rate,
        "financing_balance": financing.financing_balance,
        "amount_market_percentile": market_percentile,
    }
    missing.update(key for key, value in fields.items() if value is None)
    if ordered[-1].float_shares is None:
        missing.add("float_shares")
    if ordered[-1].sector is None:
        missing.add("sector")
    actual_generated = generated_at or datetime.now().astimezone()
    if actual_generated < data_cutoff:
        actual_generated = data_cutoff
    evidence_ids = list(
        dict.fromkeys(
            [
                *(bar.canonical_record_id for bar in ordered),
                *financing.evidence_ids,
                *((estimated_flow.evidence_ids) if estimated_flow else ()),
            ]
        )
    )
    input_hash = stable_hash(
        {
            "algorithm": CAPITAL_FLOW_ALGORITHM_VERSION,
            "symbol": symbol,
            "mode": analysis_mode.value,
            "cutoff": data_cutoff.isoformat(),
            "evidence_ids": evidence_ids,
            "market_sample": sorted(market_amounts),
        }
    )
    confidence = combined.confidence
    if not universe_complete:
        confidence = min(confidence, settings.capital_flow_partial_confidence_cap)
    return CapitalFlowSymbolSnapshot(
        snapshot_id="cfs_" + input_hash[:32],
        symbol=symbol,
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        score=combined.score,
        confidence=confidence,
        volume_score=sub_scores["volume"],
        amount_score=sub_scores["amount"],
        turnover_score=sub_scores["turnover"],
        price_volume_score=sub_scores["price_volume"],
        financing_score=sub_scores["financing"],
        sector_flow_score=sub_scores["sector_flow"],
        liquidity_score=sub_scores["liquidity"],
        volume_ratio_5d=volume.volume_ratio_5d,
        volume_ratio_20d=volume.volume_ratio_20d,
        volume_percentile_20d=volume.volume_percentile_20d,
        volume_percentile_60d=volume.volume_percentile_60d,
        consecutive_volume_expansion_days=volume.consecutive_expansion_days,
        consecutive_volume_contraction_days=volume.consecutive_contraction_days,
        amount_ratio_5d=amount.amount_ratio_5d,
        amount_ratio_20d=amount.amount_ratio_20d,
        amount_percentile_20d=amount.amount_percentile_20d,
        amount_percentile_60d=amount.amount_percentile_60d,
        amount_market_rank=rank,
        amount_market_percentile=market_percentile,
        turnover_rate=turnover.turnover_rate,
        turnover_percentile_20d=turnover.turnover_percentile_20d,
        turnover_percentile_60d=turnover.turnover_percentile_60d,
        turnover_change=turnover.turnover_change,
        consecutive_high_turnover_days=turnover.consecutive_high_turnover_days,
        price_volume_state=pv_state,
        average_amount_5d=amount.average_amount_5d,
        average_amount_20d=amount.average_amount_20d,
        zero_volume_days=zero_days,
        suspended_or_illiquid=suspended,
        liquidity_level=level,
        liquidity_confidence=liquidity_confidence,
        financing_balance=financing.financing_balance,
        financing_balance_change_1d=financing.financing_balance_change_1d,
        financing_balance_change_5d=financing.financing_balance_change_5d,
        financing_buy_amount=financing.financing_buy_amount,
        securities_lending_balance=financing.securities_lending_balance,
        financing_trend=financing.trend,
        estimated_flow=(
            {
                "classification": "ESTIMATED_FLOW",
                "value": estimated_flow.value,
                "provider": estimated_flow.provider,
                "methodology": estimated_flow.methodology,
                "confidence": min(
                    estimated_flow.confidence,
                    settings.capital_flow_estimated_flow_confidence_cap,
                ),
            }
            if estimated_flow is not None
            else None
        ),
        sector=ordered[-1].sector,
        missing_fields=sorted(missing),
        risk_flags=sorted(risks),
        evidence_ids=evidence_ids,
        input_snapshot_hash=input_hash,
        algorithm_version=CAPITAL_FLOW_ALGORITHM_VERSION,
        shadow_mode=True,
        sample_universe_size=len(market_amounts),
        partial_universe=not universe_complete,
        generated_at=actual_generated,
    )


__all__ = ["aggregate_symbol", "stable_hash"]
