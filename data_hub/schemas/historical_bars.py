"""Schemas for the read-only historical daily-bars endpoint."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

AdjustmentType = str  # RAW | FORWARD_ADJUSTED; BACKWARD_ADJUSTED is rejected


class HistoricalBarPoint(BaseModel):
    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    change_pct: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None


class HistoricalBarsResponse(BaseModel):
    symbol: str
    requested_adjustment_type: AdjustmentType
    effective_adjustment_type: AdjustmentType
    adjustment_fallback: bool
    adjustment_fallback_reason: str | None = None
    factor_anchor_date: date | None = None
    bar_count: int
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    data_cutoff: datetime | None = None
    stale: bool
    generated_at: datetime
    bars: list[HistoricalBarPoint]


__all__ = [
    "HistoricalBarPoint",
    "HistoricalBarsResponse",
]
