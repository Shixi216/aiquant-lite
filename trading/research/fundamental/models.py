from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from data_hub.schemas.unified import FactorOutput, VerificationStatus
from trading.schemas import (
    AnalysisMode,
    FundamentalDataStatus,
    FundamentalSnapshot,
)


class FundamentalRiskFlag(StrEnum):
    DATA_GAP = "DATA_GAP"
    HISTORICAL_DATA_GAP = "HISTORICAL_DATA_GAP"
    DISCLOSURE_TIME_MISSING = "DISCLOSURE_TIME_MISSING"
    FINANCIAL_CONFLICT = "FINANCIAL_CONFLICT"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_DENOMINATOR = "INVALID_DENOMINATOR"
    VALUATION_DATA_MISSING = "VALUATION_DATA_MISSING"
    STALE_FINANCIAL_DATA = "STALE_FINANCIAL_DATA"
    SINGLE_SOURCE_DATA = "SINGLE_SOURCE_DATA"
    USER_PROVIDED_DATA = "USER_PROVIDED_DATA"
    UNVERIFIED_DATA = "UNVERIFIED_DATA"
    MODE_RESTRICTION = "MODE_RESTRICTION"


class FundamentalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PointInTimeFinancialRecord(FundamentalModel):
    canonical_record_id: str
    symbol: str
    data_type: str
    report_period: str | None
    announcement_time: datetime | None
    data_available_time: datetime | None
    event_time: datetime
    fetched_at: datetime | None = None
    primary_source: str
    source_record_ids: list[str]
    verification_status: VerificationStatus
    confidence: float = Field(ge=0, le=1)
    statement_type: str | None
    statement_version: str | None = None
    revision_of_record_id: str | None = None
    accounting_scope: str | None = None
    period_type: str | None = None
    source_type: str | None = None
    disclosure_time_source: str | None = None
    payload: dict[str, Any]


class MarketValuationPoint(FundamentalModel):
    canonical_record_id: str
    source_record_ids: list[str]
    event_time: datetime
    close: float = Field(gt=0)


class FundamentalMetrics(FundamentalModel):
    pe_ttm: float | None = None
    pb: float | None = None
    roe: float | None = None
    revenue_growth: float | None = None
    net_profit_growth: float | None = None
    debt_ratio: float | None = None
    operating_cash_flow: float | None = None
    operating_cash_flow_positive: bool | None = None
    peg: float | None = None
    report_period: str | None = None
    statement_types: list[str] = Field(default_factory=list)
    accounting_scope: str | None = None
    period_type: str | None = None


class FundamentalAnalysisRequest(FundamentalModel):
    symbol: str
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    auto_fetch: bool
    manual_snapshot: FundamentalSnapshot | None = None
    allow_manual_override: bool = False
    manual_override_reason: str | None = None
    manual_operator_confirmed: bool = False


class FundamentalAnalysisResult(FundamentalModel):
    audit_id: str
    symbol: str
    analysis_mode: AnalysisMode
    status: FundamentalDataStatus
    data_cutoff: datetime
    snapshot: FundamentalSnapshot | None = None
    metrics: FundamentalMetrics
    used_report_period: str | None = None
    announcement_time: datetime | None = None
    data_available_time: datetime | None = None
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[FundamentalRiskFlag] = Field(default_factory=list)
    verification_status: str | None = None
    canonical_record_ids: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
    valuation_evidence_ids: list[str] = Field(default_factory=list)
    manual_input_id: str | None = None
    factor_output: FactorOutput | None = None
    auto_fetch_attempted: bool = False
    auto_fetch_result: str | None = None
    algorithm_version: str
    input_snapshot_hash: str


class ScreeningFundamentalSummary(FundamentalModel):
    symbol: str
    analysis_mode: AnalysisMode = AnalysisMode.SCREENING
    status: FundamentalDataStatus
    used_report_period: str | None = None
    confidence: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[FundamentalRiskFlag] = Field(default_factory=list)
    verification_status: str | None = None


class FinancialFetcher(Protocol):
    def get_financial_statement(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        persist: bool = True,
    ) -> Any: ...


__all__ = [
    "FinancialFetcher",
    "FundamentalAnalysisRequest",
    "FundamentalAnalysisResult",
    "FundamentalMetrics",
    "FundamentalRiskFlag",
    "MarketValuationPoint",
    "PointInTimeFinancialRecord",
    "ScreeningFundamentalSummary",
]
