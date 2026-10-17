"""Pydantic contract models for /webui/v2/* responses.

Blocks are intentionally loose (dict) — their *structure* is the four-key
block envelope asserted by the tests; the fixtures must validate 1:1.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class V2Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class TimeField(V2Model):
    value: str | None
    source: str
    label_zh: str


class DashboardResponse(V2Model):
    time_semantics: dict[str, TimeField]
    market_breadth: dict[str, Any]
    score_preview: dict[str, Any]
    coverage: dict[str, Any]
    components: list[dict[str, Any]]
    market_context: dict[str, Any]
    dragon_tiger: dict[str, Any]
    provenance: dict[str, Any]
    fixture_mode: bool


class OpportunityItem(V2Model):
    symbol: str
    composite_score: float
    scanner_bucket: str | None = None
    quote: dict[str, Any] | None = None


class OpportunitiesResponse(V2Model):
    classification_source: str
    ranking_data_cutoff: str | None = None
    total: int
    items: list[OpportunityItem]
    cross_section_block: dict[str, Any]
    provenance: dict[str, Any]
    fixture_mode: bool


class StockIntelResponse(V2Model):
    symbol: str
    identity: dict[str, Any]
    database_context: dict[str, Any]
    decision_card: dict[str, Any]
    four_dimensions: dict[str, Any]
    risk: dict[str, Any]
    provenance: dict[str, Any]
    fixture_mode: bool


class ChartResponse(V2Model):
    symbol: str
    bars: list[dict[str, Any]]
    data_source: str
    formal_status: str
    data_time: str | None = None
    market_coverage_date: str | None = None
    adjustment: dict[str, Any]
    tail_fill: dict[str, Any] | None = None
    unavailable_reason: str | None = None
    fixture_mode: bool


__all__ = [
    "DashboardResponse",
    "OpportunitiesResponse",
    "StockIntelResponse",
    "ChartResponse",
    "TimeField",
]
