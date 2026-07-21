from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from data_hub.schemas.market import MarketRecord


class ProviderRun(BaseModel):
    """Execution status of one financial-data provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    success: bool
    record_count: int = 0
    latency_ms: int
    error_type: str | None = None
    error_message: str | None = None


class DailyBarsResponse(BaseModel):
    """Unified response returned by the daily-bars service."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start_date: str
    end_date: str
    primary_source: str
    records: list[MarketRecord]
    provider_runs: list[ProviderRun]
    verified_dates: list[str]
    unverified_dates: list[str]


class StockBasicResponse(BaseModel):
    """Unified response returned by the stock-basic service."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    record: MarketRecord
    provider_runs: list[ProviderRun]
    verified: bool
    discrepancies: dict[str, dict[str, str | None]]

class RealtimeQuoteResponse(BaseModel):
    """Unified real-time quote response with verified fallback."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    record: MarketRecord
    provider_runs: list[ProviderRun]
    is_realtime: bool
    fallback_used: bool
    warning: str | None = None

class FinancialStatementResponse(BaseModel):
    """Unified financial-statement bundle for one report period."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    report_period: str
    records: list[MarketRecord]
    provider_runs: list[ProviderRun]
    complete: bool
    missing_statements: list[str]

class AnnouncementResponse(BaseModel):
    """Unified listed-company announcement response."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start_date: str
    end_date: str
    keyword: str
    category: str
    records: list[MarketRecord]
    provider_runs: list[ProviderRun]
    verified_count: int

class FinanceNewsResponse(BaseModel):
    """Unified finance-news response."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    query: str
    records: list[MarketRecord]
    provider_runs: list[ProviderRun]
    unverified_count: int


class MarketFactEvidence(BaseModel):
    """One stored record considered during deterministic fact verification."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    source_name: str
    source_url: str | None = None
    source_level: str
    event_time: datetime
    record_verified: bool
    observed_value: Any
    matched: bool


class MarketFactVerificationResponse(BaseModel):
    """Evidence-backed verdict for one structured market fact."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    data_type: str
    field: str
    expected_value: Any
    event_date: str | None = None
    verdict: Literal[
        "verified",
        "conflicting",
        "insufficient_evidence",
    ]
    confidence: float
    matched_sources: list[str]
    conflicting_sources: list[str]
    evidence: list[MarketFactEvidence]
