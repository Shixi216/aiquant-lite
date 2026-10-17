"""Schemas for the formal four-dimension cross-section endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CrossSectionThresholds(BaseModel):
    data_cutoff: datetime
    p50: float
    p75: float
    p90: float
    p95: float
    strong_buy_q: float
    buy_q: float
    n_total: int
    generated_at: datetime


class CrossSectionItem(BaseModel):
    symbol: str
    name: str | None = None
    industry: str | None = None
    tech_rank: float | None = None
    fund_rank: float | None = None
    sent_rank: float | None = None
    margin_rank: float | None = None
    composite_score: float


class CrossSectionLatestResponse(BaseModel):
    data_cutoff: datetime | None = None
    generated_at: datetime | None = None
    total: int
    offset: int
    limit: int
    sort: str
    min_composite: float | None = None
    thresholds: CrossSectionThresholds | None = None
    items: list[CrossSectionItem]


class CrossSectionSymbolResponse(BaseModel):
    symbol: str
    name: str | None = None
    industry: str | None = None
    score_source: str
    composite_score: float
    tech_rank: float | None = None
    fund_rank: float | None = None
    sent_rank: float | None = None
    margin_rank: float | None = None


__all__ = [
    "CrossSectionItem",
    "CrossSectionLatestResponse",
    "CrossSectionSymbolResponse",
    "CrossSectionThresholds",
]
