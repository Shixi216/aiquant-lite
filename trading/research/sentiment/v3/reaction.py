from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from trading.research.sentiment.v3.calculators import SnapshotBuildContext, _meta
from trading.research.sentiment.v3.schemas import (
    MarketSentimentSnapshot,
    QualityFlag,
    SectorSentimentSnapshot,
    SnapshotType,
    StockCapitalSnapshot,
    StockTradingSentimentSnapshot,
)
from trading.research.sentiment.v3.text_models import (
    REACTION_ALGORITHM_VERSION,
    EventIntelligenceRecord,
    EventSession,
    EventReactionSnapshot,
    ReactionState,
    ReactionWindowType,
    TextDirection,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _classification(
    direction: TextDirection, relative_market: float | None, relative_sector: float | None
) -> ReactionState:
    if relative_market is None or relative_sector is None:
        return ReactionState.INSUFFICIENT_DATA
    if relative_market == 0 and relative_sector == 0:
        return ReactionState.NO_CLEAR_REACTION
    both_positive = relative_market > 0 and relative_sector > 0
    both_negative = relative_market < 0 and relative_sector < 0
    if direction == TextDirection.POSITIVE:
        if both_positive:
            return ReactionState.CONFIRMED_POSITIVE
        if both_negative:
            return ReactionState.POSITIVE_EVENT_REJECTED
    if direction == TextDirection.NEGATIVE:
        if both_positive:
            return ReactionState.NEGATIVE_EVENT_ABSORBED
        if both_negative:
            return ReactionState.CONFIRMED_NEGATIVE
    return ReactionState.MIXED_REACTION


def calculate_event_reaction(
    *,
    context: SnapshotBuildContext,
    event: EventIntelligenceRecord,
    stock: StockTradingSentimentSnapshot,
    market: MarketSentimentSnapshot | None,
    sector: SectorSentimentSnapshot | None,
    capital: StockCapitalSnapshot | None = None,
    window_type: ReactionWindowType = ReactionWindowType.EOD_REACTION,
) -> EventReactionSnapshot:
    if stock.symbol != event.symbol:
        raise ValueError("event and stock symbols differ")
    event_local = event.available_time.astimezone(SHANGHAI)
    trade_date = stock.meta.trade_date
    must_be_later = event.event_session in {
        EventSession.IN_SESSION,
        EventSession.POST_MARKET,
        EventSession.NON_TRADING_DAY,
    }
    if trade_date < event_local.date() or (must_be_later and trade_date <= event_local.date()):
        raise ValueError("stock snapshot precedes the PIT-safe reaction session")
    reaction_start = datetime.combine(trade_date, time(9, 30), tzinfo=SHANGHAI)
    if event.event_session == EventSession.PRE_MARKET and trade_date == event_local.date():
        reaction_start = max(reaction_start, event.available_time)
    if window_type not in {
        ReactionWindowType.EOD_REACTION,
        ReactionWindowType.NEXT_SESSION_REACTION,
    }:
        raise NotImplementedError("only PIT-safe EOD and next-session windows are implemented")
    if trade_date > event_local.date():
        window_type = ReactionWindowType.NEXT_SESSION_REACTION
    reaction_end = stock.meta.source_timestamp
    if reaction_end < reaction_start or event.available_time > reaction_start:
        raise ValueError("reaction window is not after event availability")

    flags = set(context.quality_flags)
    missing: set[str] = set()
    market_return = stock.market_return if market is not None else None
    sector_return = stock.sector_return if sector is not None else None
    relative_market = (
        stock.return_pct - market_return
        if stock.return_pct is not None and market_return is not None
        else None
    )
    relative_sector = (
        stock.return_pct - sector_return
        if stock.return_pct is not None and sector_return is not None
        else None
    )
    if market is None:
        flags.add(QualityFlag.MISSING_MARKET_SNAPSHOT)
        missing.update({"market_return", "relative_market_return", "market_snapshot_id"})
    if sector is None:
        flags.add(QualityFlag.MISSING_SECTOR_SNAPSHOT)
        missing.update({"sector_return", "relative_sector_return", "sector_snapshot_id"})
    if market is not None and market.meta.available_time > context.data_cutoff:
        raise ValueError("market snapshot was not available at data_cutoff")
    if sector is not None and sector.meta.available_time > context.data_cutoff:
        raise ValueError("sector snapshot was not available at data_cutoff")
    if stock.meta.available_time > context.data_cutoff:
        raise ValueError("stock snapshot was not available at data_cutoff")
    capital_usable = capital is not None and capital.meta.available_time <= context.data_cutoff
    if capital is not None and not capital_usable:
        flags.add(QualityFlag.PIT_UNSAFE)
    classification = _classification(event.direction, relative_market, relative_sector)
    payload = {
        "event_id": event.event_id,
        "canonical_event_id": event.canonical_event_id,
        "event_direction": event.direction,
        "event_available_time": event.available_time,
        "event_session": event.event_session,
        "reaction_start": reaction_start,
        "reaction_end": reaction_end,
        "window_type": window_type,
        "classification": classification,
        "stock_return": stock.return_pct,
        "market_return": market_return,
        "sector_return": sector_return,
        "relative_market_return": relative_market,
        "relative_sector_return": relative_sector,
        "volume_change": stock.volume_change,
        "turnover_change": stock.turnover_change,
        "close_location_value": stock.close_location_value,
        "open_gap_pct": stock.open_gap_pct,
        "high_to_close_drawdown": stock.high_to_close_drawdown,
        "low_to_close_recovery": stock.low_to_close_recovery,
        "vendor_main_flow_ratio": capital.vendor_main_flow_ratio if capital_usable else None,
        "relative_market_flow": capital.relative_market_flow if capital_usable else None,
        "relative_sector_flow": capital.relative_sector_flow if capital_usable else None,
        "market_snapshot_id": market.meta.snapshot_id if market else None,
        "sector_snapshot_id": sector.meta.snapshot_id if sector else None,
        "stock_snapshot_id": stock.meta.snapshot_id,
        "capital_snapshot_id": capital.meta.snapshot_id if capital_usable else None,
        "field_provenance": {
            "price_reaction": stock.meta.snapshot_id,
            "market_context": market.meta.snapshot_id if market else "MISSING",
            "sector_context": sector.meta.snapshot_id if sector else "MISSING",
            "capital_context": capital.meta.snapshot_id if capital_usable else "MISSING",
        },
    }
    meta = _meta(
        context=context,
        snapshot_type=SnapshotType.EVENT_REACTION,
        algorithm_version=REACTION_ALGORITHM_VERSION,
        normalized_payload=payload,
        coverage_ratio=1.0 if not missing else 0.5,
        quality_flags=flags,
        missing_fields=missing,
    )
    return EventReactionSnapshot(meta=meta, **payload)


__all__ = ["calculate_event_reaction"]
