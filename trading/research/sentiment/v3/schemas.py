from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "sentiment-v3-snapshot-1.4.0"
MARKET_ALGORITHM_VERSION = "sentiment-v3-market-s2a-1.0.0"
SPECULATION_ALGORITHM_VERSION = "sentiment-v3-speculation-s2a-1.0.0"
SECTOR_ALGORITHM_VERSION = "sentiment-v3-sector-s2b-1.0.0"
STOCK_ALGORITHM_VERSION = "sentiment-v3-stock-s2b-1.0.0"
MARKET_CAPITAL_ALGORITHM_VERSION = "sentiment-v3-market-capital-s2c-1.0.0"
SECTOR_CAPITAL_ALGORITHM_VERSION = "sentiment-v3-sector-capital-s2c-1.0.0"
STOCK_CAPITAL_ALGORITHM_VERSION = "sentiment-v3-stock-capital-s2c-1.0.0"
MARGIN_ALGORITHM_VERSION = "sentiment-v3-margin-s2c-1.0.0"
DRAGON_TIGER_ALGORITHM_VERSION = "sentiment-v3-dragon-tiger-s2c-1.0.0"
UNIVERSE_RULES_VERSION = "A_SHARE_UNIVERSE_RULES_V1"


class SnapshotType(StrEnum):
    MARKET = "MARKET"
    SPECULATION = "SPECULATION"
    SECTOR = "SECTOR"
    CAPITAL = "CAPITAL"
    MARKET_CAPITAL = "MARKET_CAPITAL"
    SECTOR_CAPITAL = "SECTOR_CAPITAL"
    STOCK_CAPITAL = "STOCK_CAPITAL"
    MARGIN_FINANCING = "MARGIN_FINANCING"
    DRAGON_TIGER = "DRAGON_TIGER"
    STOCK = "STOCK"
    RETAIL_ATTENTION = "RETAIL_ATTENTION"
    NEWS = "NEWS"
    EVENT_INTELLIGENCE = "EVENT_INTELLIGENCE"
    EVENT_REACTION = "EVENT_REACTION"


class Finality(StrEnum):
    INTRADAY = "INTRADAY"
    PRELIMINARY = "PRELIMINARY"
    FINAL = "FINAL"
    CORRECTED = "CORRECTED"


class RecordSource(StrEnum):
    REAL = "REAL"
    TEST = "TEST"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


class UniverseScope(StrEnum):
    BASE_A_SHARE = "BASE_A_SHARE_UNIVERSE"
    BREADTH_ELIGIBLE = "BREADTH_ELIGIBLE_UNIVERSE"
    SECTOR_ELIGIBLE = "SECTOR_ELIGIBLE_UNIVERSE"


class SectorType(StrEnum):
    INDUSTRY = "INDUSTRY"
    CONCEPT = "CONCEPT"


class CapitalMethodologyClass(StrEnum):
    OBSERVABLE_FACT = "OBSERVABLE_FACT"
    DERIVED_TRANSPARENT = "DERIVED_TRANSPARENT"
    VENDOR_DERIVED = "VENDOR_DERIVED"
    EVENT_CAPITAL = "EVENT_CAPITAL"


class MethodologyTransparency(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class QualityFlag(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PARTIAL_DATA = "PARTIAL_DATA"
    STALE_DATA = "STALE_DATA"
    PIT_UNSAFE = "PIT_UNSAFE"
    NOT_FINAL = "NOT_FINAL"
    EOD_ONLY = "EOD_ONLY"
    SOURCE_TIMESTAMP_INFERRED = "SOURCE_TIMESTAMP_INFERRED"
    AVAILABLE_TIME_INFERRED = "AVAILABLE_TIME_INFERRED"
    PUBLISHED_TIME_UNKNOWN = "PUBLISHED_TIME_UNKNOWN"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    MISSING_EVENT = "MISSING_EVENT"
    MISSING_REACTION_WINDOW = "MISSING_REACTION_WINDOW"
    REACTION_BEFORE_EVENT = "REACTION_BEFORE_EVENT"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    MISSING_UNIVERSE = "MISSING_UNIVERSE"
    MISSING_LIMIT_UP_POOL = "MISSING_LIMIT_UP_POOL"
    MISSING_LIMIT_DOWN_POOL = "MISSING_LIMIT_DOWN_POOL"
    MISSING_BROKEN_BOARD_POOL = "MISSING_BROKEN_BOARD_POOL"
    MISSING_STRONG_STOCK_POOL = "MISSING_STRONG_STOCK_POOL"
    MISSING_PREVIOUS_MARKET_SNAPSHOT = "MISSING_PREVIOUS_MARKET_SNAPSHOT"
    MISSING_PREVIOUS_SPECULATION_SNAPSHOT = "MISSING_PREVIOUS_SPECULATION_SNAPSHOT"
    DUPLICATED_SOURCE_DATA = "DUPLICATED_SOURCE_DATA"
    SOURCE_SCHEMA_CHANGED = "SOURCE_SCHEMA_CHANGED"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    PRIMARY_SOURCE_FAILED = "PRIMARY_SOURCE_FAILED"
    FALLBACK_USED = "FALLBACK_USED"
    MARKET_BREADTH_UNAVAILABLE = "MARKET_BREADTH_UNAVAILABLE"
    MULTI_SOURCE = "MULTI_SOURCE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    CURRENT_MEMBERSHIP_ONLY = "CURRENT_MEMBERSHIP_ONLY"
    PIT_UNSAFE_FOR_HISTORY = "PIT_UNSAFE_FOR_HISTORY"
    MISSING_SECTOR_MEMBERSHIP = "MISSING_SECTOR_MEMBERSHIP"
    MISSING_MARKET_SNAPSHOT = "MISSING_MARKET_SNAPSHOT"
    MISSING_SECTOR_SNAPSHOT = "MISSING_SECTOR_SNAPSHOT"
    MISSING_PREVIOUS_QUOTE = "MISSING_PREVIOUS_QUOTE"
    MISSING_CAPITAL_DATA = "MISSING_CAPITAL_DATA"
    MISSING_MARGIN_DATA = "MISSING_MARGIN_DATA"
    MISSING_DRAGON_TIGER_DATA = "MISSING_DRAGON_TIGER_DATA"
    NEXT_SESSION_AVAILABLE = "NEXT_SESSION_AVAILABLE"
    INCOMPATIBLE_METHODOLOGY = "INCOMPATIBLE_METHODOLOGY"
    IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderHealth(SnapshotModel):
    provider: str = Field(min_length=1)
    success: bool
    latency_ms: float = Field(ge=0)
    error_type: str | None = None
    error_message_sanitized: str | None = None
    record_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    source_timestamp: datetime | None = None
    fallback_used: bool = False

    @field_validator("source_timestamp")
    @classmethod
    def require_health_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("provider source_timestamp must include a timezone")
        return value


class FieldMethodology(SnapshotModel):
    methodology_class: CapitalMethodologyClass
    transparency: MethodologyTransparency
    unit: str = Field(min_length=1)
    formula_version: str | None = None
    source_field: str | None = None


class UniverseSecurity(SnapshotModel):
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    exchange: Literal["SH", "SZ", "BJ"]
    board: str = Field(min_length=1)
    security_type: str = "STOCK"
    short_name: str | None = None
    list_date: date | None = None
    delist_date: date | None = None
    listing_status: str = Field(min_length=1)
    is_st: bool = False
    is_suspended: bool = False


class MarketUniverse(SnapshotModel):
    universe_id: str = Field(min_length=1)
    universe_version: str = Field(min_length=1)
    universe_scope: UniverseScope
    trade_date: date
    as_of: datetime
    expected_symbol_count: int = Field(ge=0)
    eligible_symbol_count: int = Field(ge=0)
    observed_symbol_count: int = Field(ge=0)
    excluded_symbol_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    source: str = Field(min_length=1)
    rules_version: str = Field(min_length=1)
    exclusion_summary: dict[str, int] = Field(default_factory=dict)
    symbols: list[str]
    symbol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_universe_version: str | None = None
    created_at: datetime

    @field_validator("as_of", "created_at")
    @classmethod
    def require_universe_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("universe timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_universe_counts(self) -> "MarketUniverse":
        if self.eligible_symbol_count != len(self.symbols):
            raise ValueError("eligible_symbol_count must equal symbols length")
        if self.observed_symbol_count > self.eligible_symbol_count:
            raise ValueError("observed count cannot exceed eligible count")
        if self.excluded_symbol_count != (self.expected_symbol_count - self.eligible_symbol_count):
            raise ValueError("excluded count must equal expected minus eligible")
        return self


class SectorMembership(SnapshotModel):
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    sector_id: str = Field(min_length=1)
    sector_name: str = Field(min_length=1)
    sector_type: SectorType
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    available_time: datetime
    source: str = Field(min_length=1)
    membership_version: str = Field(min_length=1)
    pit_safe_for_history: bool = False

    @field_validator("effective_from", "effective_to", "available_time")
    @classmethod
    def require_membership_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("membership timestamps must include a timezone")
        return value


class SentimentSnapshotMeta(SnapshotModel):
    snapshot_id: str = Field(min_length=1)
    snapshot_type: SnapshotType
    trade_date: date
    symbol_scope: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_timestamp: datetime
    available_time: datetime
    data_cutoff: datetime
    snapshot_time: datetime
    finality: Finality
    data_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_ratio: float = Field(ge=0, le=1)
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    record_source: RecordSource
    revision: int = Field(default=1, ge=1)
    corrects_snapshot_id: str | None = None
    created_at: datetime
    formal_weight: float = 0.0
    research_only: bool = True

    @field_validator(
        "source_timestamp",
        "available_time",
        "data_cutoff",
        "snapshot_time",
        "created_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot timestamps must include a timezone")
        return value

    @field_validator("quality_flags", "missing_fields")
    @classmethod
    def require_unique_sorted_lists(cls, value: list) -> list:
        if len(value) != len(set(value)):
            raise ValueError("snapshot metadata lists must be unique")
        return sorted(value, key=str)

    @model_validator(mode="after")
    def enforce_time_and_revision_contract(self) -> "SentimentSnapshotMeta":
        if not (
            self.source_timestamp
            <= self.available_time
            <= self.data_cutoff
            <= self.snapshot_time
            <= self.created_at
        ):
            raise ValueError(
                "required PIT order is source_timestamp <= available_time "
                "<= data_cutoff <= snapshot_time <= created_at"
            )
        if self.finality == Finality.CORRECTED:
            if not self.corrects_snapshot_id or self.revision <= 1:
                raise ValueError(
                    "CORRECTED snapshots require corrects_snapshot_id and revision > 1"
                )
        elif self.corrects_snapshot_id is not None:
            raise ValueError("only CORRECTED snapshots may correct another snapshot")
        if self.finality in {Finality.INTRADAY, Finality.PRELIMINARY}:
            if QualityFlag.NOT_FINAL not in self.quality_flags:
                raise ValueError("non-final snapshots require NOT_FINAL quality flag")
        return self


OptionalCount = Annotated[int | None, Field(default=None, ge=0)]
OptionalRatio = Annotated[float | None, Field(default=None, ge=0)]


class MarketSentimentSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    universe_size: OptionalCount = None
    valid_quote_count: OptionalCount = None
    advancers: OptionalCount = None
    decliners: OptionalCount = None
    unchanged: OptionalCount = None
    advance_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    decline_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    advance_decline_ratio: OptionalRatio = None
    breadth_denominator: OptionalCount = None
    breadth_strength: Annotated[float | None, Field(default=None, ge=-1, le=1)] = None
    limit_up_count: OptionalCount = None
    limit_down_count: OptionalCount = None
    limit_up_down_ratio: OptionalRatio = None
    broken_board_count: OptionalCount = None
    sealed_limit_up_count: OptionalCount = None
    broken_board_denominator: OptionalCount = None
    broken_board_denominator_definition: str | None = None
    broken_board_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    strong_stock_count: OptionalCount = None
    market_turnover: Annotated[float | None, Field(default=None, ge=0)] = None
    previous_market_turnover: Annotated[float | None, Field(default=None, ge=0)] = None
    market_turnover_change: float | None = None
    limit_up_symbols: list[str] | None = None
    limit_down_symbols: list[str] | None = None
    broken_board_symbols: list[str] | None = None
    strong_stock_symbols: list[str] | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    provider_health: list[ProviderHealth] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_type_and_missing_values(self) -> "MarketSentimentSnapshot":
        if self.meta.snapshot_type != SnapshotType.MARKET:
            raise ValueError("market snapshot requires MARKET metadata")
        return self


class SpeculationSentimentSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    first_board_count: OptionalCount = None
    second_board_count: OptionalCount = None
    third_board_count: OptionalCount = None
    high_board_count: OptionalCount = None
    max_board_height: OptionalCount = None
    board_ladder_distribution: dict[str, int] | None = None
    limit_up_symbols: list[str] | None = None
    board_height_by_symbol: dict[str, int] | None = None
    broken_board_count: OptionalCount = None
    broken_board_symbols: list[str] | None = None
    sealed_limit_up_count: OptionalCount = None
    broken_board_denominator: OptionalCount = None
    broken_board_denominator_definition: str | None = None
    broken_board_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    strong_stock_count: OptionalCount = None
    strong_stock_symbols: list[str] | None = None
    promotion_count: OptionalCount = None
    promotion_denominator: OptionalCount = None
    promotion_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    yesterday_limit_up_return_count: OptionalCount = None
    yesterday_limit_up_avg_return: float | None = None
    yesterday_limit_up_median_return: float | None = None
    yesterday_board_success_count: OptionalCount = None
    yesterday_board_success_denominator: OptionalCount = None
    yesterday_board_success_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    broken_board_repair_count: OptionalCount = None
    broken_board_repair_denominator: OptionalCount = None
    broken_board_repair_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    high_position_negative_feedback_count: OptionalCount = None
    high_position_negative_feedback_denominator: OptionalCount = None
    high_position_negative_feedback: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    previous_snapshot_id: str | None = None

    @model_validator(mode="after")
    def validate_type(self) -> "SpeculationSentimentSnapshot":
        if self.meta.snapshot_type != SnapshotType.SPECULATION:
            raise ValueError("speculation snapshot requires SPECULATION metadata")
        return self


class SectorSentimentSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    sector_id: str
    sector_name: str
    sector_type: SectorType
    universe_id: str
    universe_version: str
    market_snapshot_id: str | None
    membership_version: str
    member_count: OptionalCount = None
    observed_member_count: OptionalCount = None
    advancers: OptionalCount = None
    decliners: OptionalCount = None
    unchanged: OptionalCount = None
    advance_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    mean_return: float | None = None
    median_return: float | None = None
    sector_turnover: Annotated[float | None, Field(default=None, ge=0)] = None
    previous_sector_turnover: Annotated[float | None, Field(default=None, ge=0)] = None
    turnover_change: float | None = None
    volume_change: float | None = None
    limit_up_count: OptionalCount = None
    limit_down_count: OptionalCount = None
    broken_board_count: OptionalCount = None
    limit_up_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    broken_board_rate: Annotated[float | None, Field(default=None, ge=0, le=1)] = None
    market_return: float | None = None
    market_advance_ratio: float | None = None
    relative_market_return: float | None = None
    relative_market_breadth: float | None = None
    leader_symbol: str | None = None
    leader_return: float | None = None
    leader_definition: str = "RETURN_RANK_V1"
    top_n: int = Field(default=3, ge=1)
    top_n_mean_return: float | None = None
    top_n_median_return: float | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_sector_type(self) -> "SectorSentimentSnapshot":
        if self.meta.snapshot_type != SnapshotType.SECTOR:
            raise ValueError("sector snapshot requires SECTOR metadata")
        return self


class StockTradingSentimentSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    universe_id: str
    universe_version: str
    industry_sector_id: str | None = None
    concept_sector_ids: list[str] = Field(default_factory=list)
    return_pct: float | None = None
    open_gap_pct: float | None = None
    intraday_return_pct: float | None = None
    amplitude: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    volume_change: float | None = None
    turnover_change: float | None = None
    close_location_value: float | None = None
    close_location_version: str = "CLV_V1"
    high_to_close_drawdown: float | None = None
    low_to_close_recovery: float | None = None
    gap_and_fade: float | None = None
    gap_and_follow_through: float | None = None
    gap_path_version: str = "GAP_PATH_V1"
    market_return: float | None = None
    sector_return: float | None = None
    relative_market_return: float | None = None
    relative_sector_return: float | None = None
    market_volume_change: float | None = None
    sector_volume_change: float | None = None
    relative_market_volume: float | None = None
    relative_sector_volume: float | None = None
    is_limit_up: bool | None = None
    is_limit_down: bool | None = None
    is_broken_board: bool | None = None
    market_snapshot_id: str | None
    sector_snapshot_id: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stock_type(self) -> "StockTradingSentimentSnapshot":
        if self.meta.snapshot_type != SnapshotType.STOCK:
            raise ValueError("stock snapshot requires STOCK metadata")
        return self


class MarketCapitalSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    universe_id: str
    universe_version: str
    market_snapshot_id: str | None = None
    expected_symbol_count: OptionalCount = None
    observed_symbol_count: OptionalCount = None
    market_turnover: float | None = None
    previous_market_turnover: float | None = None
    market_turnover_change: float | None = None
    vendor_total_buy_amount: float | None = None
    vendor_total_sell_amount: float | None = None
    vendor_net_flow: float | None = None
    vendor_main_net_flow: float | None = None
    vendor_main_flow_ratio: float | None = None
    margin_snapshot_id: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_methodology: dict[str, FieldMethodology] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "MarketCapitalSnapshot":
        if self.meta.snapshot_type != SnapshotType.MARKET_CAPITAL:
            raise ValueError("market capital snapshot requires MARKET_CAPITAL metadata")
        return self


class SectorCapitalSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    sector_id: str
    sector_name: str
    sector_type: SectorType
    universe_id: str
    universe_version: str
    membership_version: str
    market_capital_snapshot_id: str | None = None
    sector_snapshot_id: str | None = None
    member_count: OptionalCount = None
    observed_member_count: OptionalCount = None
    sector_turnover: float | None = None
    previous_sector_turnover: float | None = None
    turnover_change: float | None = None
    vendor_main_flow_sum: float | None = None
    vendor_main_flow_median: float | None = None
    vendor_large_flow_sum: float | None = None
    vendor_small_flow_sum: float | None = None
    vendor_main_flow_ratio: float | None = None
    relative_market_turnover: float | None = None
    relative_market_flow: float | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_methodology: dict[str, FieldMethodology] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "SectorCapitalSnapshot":
        if self.meta.snapshot_type != SnapshotType.SECTOR_CAPITAL:
            raise ValueError("sector capital snapshot requires SECTOR_CAPITAL metadata")
        return self


class StockCapitalSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    universe_id: str
    universe_version: str
    industry_sector_id: str | None = None
    turnover: float | None = None
    previous_turnover: float | None = None
    turnover_change: float | None = None
    turnover_rate: float | None = None
    vendor_main_net_flow: float | None = None
    vendor_super_large_net_flow: float | None = None
    vendor_large_net_flow: float | None = None
    vendor_medium_net_flow: float | None = None
    vendor_small_net_flow: float | None = None
    vendor_main_flow_ratio: float | None = None
    market_vendor_main_flow_ratio: float | None = None
    sector_vendor_main_flow_ratio: float | None = None
    relative_market_flow: float | None = None
    relative_sector_flow: float | None = None
    market_capital_snapshot_id: str | None = None
    sector_capital_snapshot_id: str | None = None
    stock_snapshot_id: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_methodology: dict[str, FieldMethodology] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "StockCapitalSnapshot":
        if self.meta.snapshot_type != SnapshotType.STOCK_CAPITAL:
            raise ValueError("stock capital snapshot requires STOCK_CAPITAL metadata")
        return self


class MarginFinancingSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    universe_id: str
    universe_version: str
    has_margin_data: bool | None = None
    margin_balance: float | None = None
    margin_buy: float | None = None
    margin_repay: float | None = None
    margin_net_buy: float | None = None
    short_balance: float | None = None
    short_sell: float | None = None
    short_repay: float | None = None
    total_margin_balance: float | None = None
    margin_balance_change: float | None = None
    margin_balance_change_pct: float | None = None
    market_margin_total: float | None = None
    previous_margin_snapshot_id: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_methodology: dict[str, FieldMethodology] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "MarginFinancingSnapshot":
        if self.meta.snapshot_type != SnapshotType.MARGIN_FINANCING:
            raise ValueError("margin snapshot requires MARGIN_FINANCING metadata")
        return self


class DragonTigerSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    universe_id: str
    universe_version: str
    has_dragon_tiger: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    buy_amount: float | None = None
    sell_amount: float | None = None
    net_amount: float | None = None
    institution_buy_amount: float | None = None
    institution_sell_amount: float | None = None
    institution_net_amount: float | None = None
    seat_count: OptionalCount = None
    institution_seat_count: OptionalCount = None
    top_buyers: list[str] = Field(default_factory=list)
    top_sellers: list[str] = Field(default_factory=list)
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_methodology: dict[str, FieldMethodology] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "DragonTigerSnapshot":
        if self.meta.snapshot_type != SnapshotType.DRAGON_TIGER:
            raise ValueError("dragon tiger snapshot requires DRAGON_TIGER metadata")
        if self.has_dragon_tiger is False and any(
            value is not None for value in (self.buy_amount, self.sell_amount, self.net_amount)
        ):
            raise ValueError("not-listed dragon tiger snapshot cannot contain event amounts")
        return self


Snapshot = (
    MarketSentimentSnapshot
    | SpeculationSentimentSnapshot
    | SectorSentimentSnapshot
    | StockTradingSentimentSnapshot
    | MarketCapitalSnapshot
    | SectorCapitalSnapshot
    | StockCapitalSnapshot
    | MarginFinancingSnapshot
    | DragonTigerSnapshot
)


__all__ = [
    "CapitalMethodologyClass",
    "DRAGON_TIGER_ALGORITHM_VERSION",
    "DragonTigerSnapshot",
    "FieldMethodology",
    "Finality",
    "MARGIN_ALGORITHM_VERSION",
    "MARKET_CAPITAL_ALGORITHM_VERSION",
    "MarginFinancingSnapshot",
    "MarketCapitalSnapshot",
    "MethodologyTransparency",
    "MARKET_ALGORITHM_VERSION",
    "MarketSentimentSnapshot",
    "MarketUniverse",
    "ProviderHealth",
    "QualityFlag",
    "RecordSource",
    "SCHEMA_VERSION",
    "SECTOR_ALGORITHM_VERSION",
    "SECTOR_CAPITAL_ALGORITHM_VERSION",
    "STOCK_ALGORITHM_VERSION",
    "STOCK_CAPITAL_ALGORITHM_VERSION",
    "UNIVERSE_RULES_VERSION",
    "SPECULATION_ALGORITHM_VERSION",
    "SectorMembership",
    "SectorCapitalSnapshot",
    "SectorSentimentSnapshot",
    "SectorType",
    "SentimentSnapshotMeta",
    "Snapshot",
    "SnapshotType",
    "SpeculationSentimentSnapshot",
    "StockCapitalSnapshot",
    "StockTradingSentimentSnapshot",
    "UniverseScope",
    "UniverseSecurity",
]
