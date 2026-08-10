from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class FullMarketModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    CONFLICT = "CONFLICT"
    UNVERIFIED = "UNVERIFIED"


class AnalysisMode(StrEnum):
    SCREENING = "SCREENING"
    RESEARCH = "RESEARCH"
    DECISION = "DECISION"


class ListingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class AliasType(StrEnum):
    COMPANY_FULL_NAME = "COMPANY_FULL_NAME"
    CURRENT_SHORT_NAME = "CURRENT_SHORT_NAME"
    HISTORICAL_SHORT_NAME = "HISTORICAL_SHORT_NAME"
    ENGLISH_NAME = "ENGLISH_NAME"
    COMMON_ABBREVIATION = "COMMON_ABBREVIATION"


class SnapshotCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL_UNIVERSE = "PARTIAL_UNIVERSE"
    FAILED = "FAILED"


class ExpansionType(StrEnum):
    DAILY_BARS = "DAILY_BARS"
    ANNOUNCEMENTS = "ANNOUNCEMENTS"
    FINANCE_NEWS = "FINANCE_NEWS"
    ENTITY_LINKS = "ENTITY_LINKS"
    DAILY_UPDATE = "DAILY_UPDATE"


class ProviderCapability(FullMarketModel):
    provider: str
    capability: str
    available: bool
    verified_at: datetime
    failure_reason: str | None = None
    batch_supported: bool
    maximum_batch_size: int | None = Field(default=None, ge=1)
    rate_limit: str | None = None
    requires_permission: bool = False
    fallback_provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_verified_at = field_validator("verified_at")(_timezone)


class StockUniverseRecord(FullMarketModel):
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    exchange: str
    market: str
    board: str
    security_type: str = "STOCK"
    company_name: str | None = None
    short_name: str | None = None
    list_date: date | None = None
    delist_date: date | None = None
    listing_status: ListingStatus
    is_st: bool = False
    is_suspended: bool = False
    currency: str = "CNY"
    price_limit_type: str
    primary_source: str
    source_record_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    source_differences: dict[str, Any] = Field(default_factory=dict)
    data_available_time: datetime
    updated_at: datetime
    universe_version: str

    _validate_available = field_validator("data_available_time")(_timezone)
    _validate_updated = field_validator("updated_at")(_timezone)


class StockAlias(FullMarketModel):
    alias_id: str
    symbol: str
    alias_name: str
    alias_type: AliasType
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source: str
    verification_status: VerificationStatus
    normalized_alias: str
    generated_at: datetime
    universe_version: str

    _validate_from = field_validator("valid_from")(_timezone)
    _validate_to = field_validator("valid_to")(_timezone)
    _validate_generated = field_validator("generated_at")(_timezone)

    @model_validator(mode="after")
    def validate_window(self) -> "StockAlias":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be later than valid_to")
        return self


class IndustryMembership(FullMarketModel):
    membership_id: str
    symbol: str
    industry_code: str | None = None
    industry_name: str
    industry_level: str
    classification_system: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source: str
    verification_status: VerificationStatus
    mapping_version: str
    generated_at: datetime

    _validate_from = field_validator("valid_from")(_timezone)
    _validate_to = field_validator("valid_to")(_timezone)
    _validate_generated = field_validator("generated_at")(_timezone)


class MarketSnapshotItem(FullMarketModel):
    symbol: str
    price: float | None = None
    previous_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    change: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = Field(default=None, ge=0)
    snapshot_time: datetime
    source: str
    item_status: str
    is_suspended: bool = False
    is_abnormal: bool = False
    raw_record_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    _validate_snapshot_time = field_validator("snapshot_time")(_timezone)


class UniverseSyncRequest(FullMarketModel):
    analysis_mode: AnalysisMode = AnalysisMode.RESEARCH
    dry_run: bool = True
    provider: str = "AUTO"
    request_budget: int = Field(default=3, ge=0, le=20)
    data_cutoff: datetime
    report_path: str | None = None

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


class UniverseSyncResponse(FullMarketModel):
    run_id: str
    mode: str
    provider: str
    request_count: int = Field(ge=0)
    universe_version: str | None
    received_count: int = Field(ge=0)
    persisted_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    board_counts: dict[str, int]
    alias_count: int = Field(ge=0)
    industry_count: int = Field(ge=0)
    status: str
    warnings: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0)


class UniverseListResponse(FullMarketModel):
    universe_version: str | None
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: list[StockUniverseRecord]


class MarketSnapshotSyncRequest(FullMarketModel):
    analysis_mode: AnalysisMode = AnalysisMode.SCREENING
    dry_run: bool = True
    provider: str = "AKSHARE"
    request_budget: int = Field(default=1, ge=0, le=5)
    data_cutoff: datetime
    maximum_age_seconds: int = Field(default=300, ge=0, le=86_400)
    limit: int | None = Field(default=None, ge=1, le=10_000)

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


class MarketSnapshotResponse(FullMarketModel):
    snapshot_id: str
    mode: str
    provider: str
    data_cutoff: datetime
    snapshot_time: datetime
    expected_universe_size: int = Field(ge=0)
    received_symbol_count: int = Field(ge=0)
    valid_symbol_count: int = Field(ge=0)
    missing_symbol_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    completeness_status: SnapshotCompleteness
    stale: bool = False
    original_snapshot_time: datetime
    data_age_seconds: float = Field(ge=0)
    request_count: int = Field(ge=0)
    items: list[MarketSnapshotItem] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)

    _validate_cutoff = field_validator("data_cutoff")(_timezone)
    _validate_snapshot = field_validator("snapshot_time")(_timezone)
    _validate_original = field_validator("original_snapshot_time")(_timezone)


class DataExpansionRequest(FullMarketModel):
    analysis_mode: AnalysisMode = AnalysisMode.RESEARCH
    dry_run: bool = True
    provider: str = "AUTO"
    request_budget: int = Field(default=10, ge=0, le=10_000)
    data_cutoff: datetime
    expansion_type: ExpansionType
    symbols: list[str] = Field(default_factory=list, max_length=5000)
    trade_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    batch_size: int = Field(default=50, ge=1, le=1000)
    resume: bool = False
    report_path: str | None = None

    _validate_cutoff = field_validator("data_cutoff")(_timezone)

    @model_validator(mode="after")
    def validate_dates(self) -> "DataExpansionRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        if (
            self.analysis_mode == AnalysisMode.SCREENING
            and self.expansion_type == ExpansionType.DAILY_BARS
            and self.request_budget > 0
        ):
            raise ValueError("SCREENING cannot perform per-symbol data fetching")
        return self


class DataExpansionResponse(FullMarketModel):
    run_id: str
    expansion_type: ExpansionType
    mode: str
    analysis_mode: AnalysisMode
    provider: str
    request_budget: int
    request_count: int
    processed_count: int
    success_count: int
    skipped_count: int
    conflict_count: int
    failed_count: int
    status: str
    warnings: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0)


class CandidateEnrichmentRequest(FullMarketModel):
    analysis_mode: AnalysisMode
    dry_run: bool = True
    provider: str = "AUTO"
    request_budget: int = Field(default=0, ge=0, le=100)
    data_cutoff: datetime
    symbols: list[str] = Field(min_length=1, max_length=20)
    minimum_history_days: int = Field(default=60, ge=1, le=250)
    model_call_budget: int = Field(default=0, ge=0, le=10)

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


class CandidateEnrichmentItem(FullMarketModel):
    symbol: str
    enrichment_status: str
    fetched_data_types: list[str]
    missing_data_types: list[str]
    elapsed_time: float = Field(ge=0)
    risk_flags: list[str]
    request_count: int = Field(ge=0)
    model_call_count: int = Field(default=0, ge=0)
    error_message: str | None = None


class CandidateEnrichmentResponse(FullMarketModel):
    run_id: str
    mode: str
    analysis_mode: AnalysisMode
    candidate_count: int
    request_count: int
    model_call_count: int
    status: str
    items: list[CandidateEnrichmentItem]


class CoverageMetric(FullMarketModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)


class DataCoverageResponse(FullMarketModel):
    coverage_id: str
    data_cutoff: datetime
    generated_at: datetime
    universe_version: str | None
    metrics: dict[str, CoverageMetric]
    counts: dict[str, int]
    latest_successful_updates: dict[str, datetime | None]
    warnings: list[str]
    content_hash: str

    _validate_cutoff = field_validator("data_cutoff")(_timezone)
    _validate_generated = field_validator("generated_at")(_timezone)


__all__ = [
    "AnalysisMode",
    "AliasType",
    "CandidateEnrichmentItem",
    "CandidateEnrichmentRequest",
    "CandidateEnrichmentResponse",
    "CoverageMetric",
    "DataCoverageResponse",
    "DataExpansionRequest",
    "DataExpansionResponse",
    "ExpansionType",
    "IndustryMembership",
    "ListingStatus",
    "MarketSnapshotItem",
    "MarketSnapshotResponse",
    "MarketSnapshotSyncRequest",
    "ProviderCapability",
    "SnapshotCompleteness",
    "StockAlias",
    "StockUniverseRecord",
    "UniverseListResponse",
    "UniverseSyncRequest",
    "UniverseSyncResponse",
    "VerificationStatus",
]
