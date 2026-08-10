from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from data_hub.schemas.full_market import CoverageMetric


class HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value


class AdjustmentType(StrEnum):
    RAW = "RAW"
    FORWARD_ADJUSTED = "FORWARD_ADJUSTED"
    BACKWARD_ADJUSTED = "BACKWARD_ADJUSTED"


class BackfillStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    SUCCESS = "SUCCESS"
    SUCCESS_PARTIAL = "SUCCESS_PARTIAL"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    FAILED = "FAILED"
    PAUSED_BUDGET = "PAUSED_BUDGET"
    PAUSED_RATE_LIMIT = "PAUSED_RATE_LIMIT"
    CANCELLED = "CANCELLED"


class SelectionStrategy(StrEnum):
    EXPLICIT = "EXPLICIT"
    LIQUIDITY = "LIQUIDITY"
    SYMBOL_RANGE = "SYMBOL_RANGE"
    TRADE_DATE = "TRADE_DATE"


class ShardType(StrEnum):
    SYMBOL_SHARD = "SYMBOL_SHARD"
    TRADE_DATE_SHARD = "TRADE_DATE_SHARD"


class TradeDateShardStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    SUCCESS_PARTIAL = "SUCCESS_PARTIAL"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    FAILED = "FAILED"
    PAUSED_BUDGET = "PAUSED_BUDGET"
    PAUSED_RATE_LIMIT = "PAUSED_RATE_LIMIT"
    CANCELLED = "CANCELLED"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NEWLY_LISTED_20D = "NEWLY_LISTED_20D"
    NEWLY_LISTED_60D = "NEWLY_LISTED_60D"
    LONG_SUSPENDED = "LONG_SUSPENDED"
    UNKNOWN_LIST_DATE = "UNKNOWN_LIST_DATE"
    NOT_ACTIVE = "NOT_ACTIVE"


class HistoryBackfillRequest(HistoryModel):
    dry_run: bool = True
    shard_type: ShardType = ShardType.SYMBOL_SHARD
    provider: str = "AUTO"
    fallback_providers: list[str] = Field(
        default_factory=lambda: ["BAOSTOCK", "AKSHARE"],
        max_length=3,
    )
    symbols: list[str] = Field(default_factory=list, max_length=6000)
    board: str | None = None
    exchange: str | None = None
    start_symbol: str | None = None
    end_symbol: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    trade_dates: list[date] = Field(default_factory=list, max_length=60)
    start_trade_date: date | None = None
    end_trade_date: date | None = None
    target_trading_days: int = Field(default=60, ge=1, le=250)
    batch_size: int = Field(default=100, ge=1, le=300)
    concurrency: int = Field(default=1, ge=1, le=3)
    request_budget: int = Field(default=100, ge=0, le=10_000)
    max_retries: int = Field(default=1, ge=0, le=3)
    resume: bool = False
    verify_idempotency: bool = False
    data_cutoff: datetime
    adjustment_type: AdjustmentType = AdjustmentType.RAW
    selection_strategy: SelectionStrategy = SelectionStrategy.EXPLICIT
    max_symbols: int = Field(default=300, ge=1, le=6000)
    minimum_free_bytes: int = Field(default=2_147_483_648, ge=0)
    report_path: str | None = None

    _validate_cutoff = field_validator("data_cutoff")(_timezone)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"AUTO", "BAOSTOCK", "AKSHARE", "TUSHARE"}:
            raise ValueError("unsupported history provider")
        return normalized

    @field_validator("fallback_providers")
    @classmethod
    def normalize_fallbacks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        allowed = {"BAOSTOCK", "AKSHARE", "TUSHARE"}
        if any(value not in allowed for value in normalized):
            raise ValueError("unsupported fallback provider")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_scope_and_dates(self) -> "HistoryBackfillRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        cutoff_date = self.data_cutoff.astimezone().date()
        if self.end_date and self.end_date > cutoff_date:
            raise ValueError("end_date must not be later than data_cutoff")
        if (
            self.start_symbol
            and self.end_symbol
            and self.start_symbol > self.end_symbol
        ):
            raise ValueError("start_symbol must not be later than end_symbol")
        if (
            self.start_trade_date
            and self.end_trade_date
            and self.start_trade_date > self.end_trade_date
        ):
            raise ValueError(
                "start_trade_date must not be later than end_trade_date"
            )
        if self.shard_type == ShardType.TRADE_DATE_SHARD:
            if self.provider != "TUSHARE":
                raise ValueError(
                    "TRADE_DATE_SHARD requires provider=TUSHARE"
                )
            if self.adjustment_type != AdjustmentType.RAW:
                raise ValueError("TRADE_DATE_SHARD is RAW-only")
            if self.concurrency != 1:
                raise ValueError("TRADE_DATE_SHARD concurrency must be 1")
            if self.symbols or any(
                (
                    self.board,
                    self.exchange,
                    self.start_symbol,
                    self.end_symbol,
                )
            ):
                raise ValueError(
                    "TRADE_DATE_SHARD cannot mix symbol selection"
                )
            if self.trade_dates and (
                self.start_trade_date or self.end_trade_date
            ):
                raise ValueError(
                    "trade_dates cannot be combined with a trade-date range"
                )
        elif (
            self.selection_strategy == SelectionStrategy.EXPLICIT
            and not self.symbols
        ):
            raise ValueError("EXPLICIT selection requires symbols")
        if self.concurrency > self.batch_size:
            raise ValueError("concurrency cannot exceed batch_size")
        return self


class HistoryBackfillItem(HistoryModel):
    symbol: str
    eligibility_status: EligibilityStatus
    list_date: date | None = None
    expected_trading_days: int = Field(ge=0)
    observed_trading_days: int = Field(ge=0)
    provider_used: str | None = None
    status: str
    attempt_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    raw_record_count: int = Field(ge=0)
    canonical_record_count: int = Field(ge=0)
    fetch_elapsed_seconds: float = Field(ge=0)
    write_elapsed_seconds: float = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None


class HistoryTradeDateShard(HistoryModel):
    run_id: str
    shard_id: str
    shard_type: ShardType = ShardType.TRADE_DATE_SHARD
    provider: str
    trade_date: date
    expected_universe_count: int = Field(ge=0)
    returned_record_count: int = Field(ge=0)
    valid_record_count: int = Field(ge=0)
    invalid_record_count: int = Field(ge=0)
    missing_symbol_count: int = Field(ge=0)
    extra_symbol_count: int = Field(ge=0)
    beijing_record_count: int = Field(ge=0)
    shanghai_record_count: int = Field(ge=0)
    shenzhen_record_count: int = Field(ge=0)
    raw_inserted_count: int = Field(ge=0)
    canonical_inserted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    write_elapsed_seconds: float = Field(ge=0)
    database_growth_bytes: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    status: TradeDateShardStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    sanitized_error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class HistoryBackfillResponse(HistoryModel):
    run_id: str
    mode: str
    provider: str
    status: BackfillStatus
    requested_symbol_count: int = Field(ge=0)
    eligible_symbol_count: int = Field(ge=0)
    completed_symbol_count: int = Field(ge=0)
    successful_symbol_count: int = Field(ge=0)
    skipped_symbol_count: int = Field(ge=0)
    failed_symbol_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    request_budget: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    resume_cursor: int = Field(ge=0)
    database_bytes_before: int = Field(ge=0)
    database_bytes_after: int | None = Field(default=None, ge=0)
    persisted_raw_count: int = Field(ge=0)
    persisted_canonical_count: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime | None
    warnings: list[str] = Field(default_factory=list)
    items: list[HistoryBackfillItem] = Field(default_factory=list)
    shard_type: ShardType = ShardType.SYMBOL_SHARD
    requested_trade_date_count: int = Field(default=0, ge=0)
    completed_trade_date_count: int = Field(default=0, ge=0)
    date_shards: list[HistoryTradeDateShard] = Field(default_factory=list)

    _validate_started = field_validator("started_at")(_timezone)


class HistoryRunActionRequest(HistoryModel):
    apply: bool = False
    data_cutoff: datetime
    request_budget: int | None = Field(default=None, ge=1, le=10_000)
    verify_idempotency: bool = False

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


class HistoryRunDetail(HistoryModel):
    run: dict[str, Any]
    shards: list[dict[str, Any]]
    items: list[dict[str, Any]]
    request_audits: list[dict[str, Any]]
    date_shards: list[dict[str, Any]] = Field(default_factory=list)


class HistoryEligibilityCounts(HistoryModel):
    active_universe_count: int = Field(ge=0)
    eligible_20d_count: int = Field(ge=0)
    eligible_60d_count: int = Field(ge=0)
    long_suspended_count: int = Field(ge=0)
    special_status_count: int = Field(ge=0)
    newly_listed_20d_count: int = Field(ge=0)
    newly_listed_60d_count: int = Field(ge=0)
    unknown_list_date_count: int = Field(ge=0)
    failed_collection_count: int = Field(ge=0)


class HistoryCoverageResponse(HistoryModel):
    data_cutoff: datetime
    analysis_data_cutoff: datetime
    as_of_trade_date: date | None
    report_generated_at: datetime
    adjustment_type: AdjustmentType
    eligibility: HistoryEligibilityCounts
    history_20d: CoverageMetric
    history_60d: CoverageMetric
    conflict_bar_count: int = Field(ge=0)
    single_source_bar_count: int = Field(ge=0)
    latest_completed_trade_date: date | None
    required_trade_dates_20d: list[date] = Field(default_factory=list)
    required_trade_dates_60d: list[date] = Field(default_factory=list)
    calendar_providers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


class ProviderVerificationResult(HistoryModel):
    provider: str
    capability: str
    status: str
    request_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    failure_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderVerificationResponse(HistoryModel):
    data_cutoff: datetime
    tushare_token_present: bool
    total_request_count: int = Field(ge=0)
    results: list[ProviderVerificationResult]

    _validate_cutoff = field_validator("data_cutoff")(_timezone)


__all__ = [
    "AdjustmentType",
    "BackfillStatus",
    "EligibilityStatus",
    "HistoryBackfillItem",
    "HistoryBackfillRequest",
    "HistoryBackfillResponse",
    "HistoryCoverageResponse",
    "HistoryEligibilityCounts",
    "HistoryRunActionRequest",
    "HistoryRunDetail",
    "HistoryTradeDateShard",
    "ProviderVerificationResponse",
    "ProviderVerificationResult",
    "SelectionStrategy",
    "ShardType",
    "TradeDateShardStatus",
]
