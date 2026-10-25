"""Schemas for the server-side full-universe market breadth endpoint."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class ChangeBuckets(BaseModel):
    gt7: int = 0
    p5_7: int = 0
    p2_5: int = 0
    p0_2: int = 0
    flat: int = 0
    n0_2: int = 0
    n2_5: int = 0
    n5_7: int = 0
    lt7: int = 0


class MarketBreadthResponse(BaseModel):
    snapshot_id: str
    snapshot_time: datetime
    data_cutoff: datetime
    expected_universe_size: int
    received_symbol_count: int
    valid_symbol_count: int
    coverage_ratio: float
    completeness_status: str
    stale: bool
    data_age_seconds: float
    advances: int
    declines: int
    flats: int
    limit_ups: int | None = None
    limit_downs: int | None = None
    limit_counts_derivation: str = (
        "unavailable: snapshot carries no price-limit context "
        "(no ST flag / board limit), not guessed from fixed 10%"
    )
    total_amount: float | None = None
    median_change_pct: float | None = None
    change_buckets: ChangeBuckets
    market_trade_date: date | None = None
    market_trade_date_status: str | None = None
    market_trade_date_derivation: str
    snapshot_data_trade_date: date | None = None
    bars_max_trade_date: date | None = None
    partial_universe: bool


__all__ = ["ChangeBuckets", "MarketBreadthResponse"]
