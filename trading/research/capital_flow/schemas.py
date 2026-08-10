from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.schemas import AnalysisMode


class CapitalFlowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapitalFlowRiskFlag(StrEnum):
    DATA_GAP = "DATA_GAP"
    PARTIAL_UNIVERSE = "PARTIAL_UNIVERSE"
    PARTIAL_SECTOR = "PARTIAL_SECTOR"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNIT_UNKNOWN = "UNIT_UNKNOWN"
    UNIT_CONFLICT = "UNIT_CONFLICT"
    TURNOVER_DATA_MISSING = "TURNOVER_DATA_MISSING"
    FLOAT_SHARES_MISSING = "FLOAT_SHARES_MISSING"
    FINANCING_DATA_MISSING = "FINANCING_DATA_MISSING"
    HIGH_TURNOVER = "HIGH_TURNOVER"
    EXTREME_VOLUME_SPIKE = "EXTREME_VOLUME_SPIKE"
    EXTREME_AMOUNT_SPIKE = "EXTREME_AMOUNT_SPIKE"
    PRICE_UP_VOLUME_DOWN = "PRICE_UP_VOLUME_DOWN"
    PRICE_DOWN_VOLUME_UP = "PRICE_DOWN_VOLUME_UP"
    POSSIBLE_DISTRIBUTION = "POSSIBLE_DISTRIBUTION"
    POSSIBLE_SPECULATION = "POSSIBLE_SPECULATION"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    SUSPENDED_OR_ILLIQUID = "SUSPENDED_OR_ILLIQUID"
    ESTIMATED_FLOW_ONLY = "ESTIMATED_FLOW_ONLY"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    MODE_RESTRICTION = "MODE_RESTRICTION"


class PriceVolumeState(StrEnum):
    PRICE_UP_VOLUME_UP = "PRICE_UP_VOLUME_UP"
    PRICE_UP_VOLUME_DOWN = "PRICE_UP_VOLUME_DOWN"
    PRICE_DOWN_VOLUME_UP = "PRICE_DOWN_VOLUME_UP"
    PRICE_DOWN_VOLUME_DOWN = "PRICE_DOWN_VOLUME_DOWN"
    PRICE_FLAT_VOLUME_UP = "PRICE_FLAT_VOLUME_UP"
    PRICE_FLAT_VOLUME_DOWN = "PRICE_FLAT_VOLUME_DOWN"
    PRICE_VOLUME_NEUTRAL = "PRICE_VOLUME_NEUTRAL"
    UNKNOWN = "UNKNOWN"


class FinancingTrend(StrEnum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    VOLATILE = "VOLATILE"
    MISSING = "MISSING"


class LiquidityLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


class CapitalFlowScope(StrEnum):
    SYMBOL = "SYMBOL"
    SECTOR = "SECTOR"
    MARKET = "MARKET"


class SubScore(CapitalFlowModel):
    value: float | None = Field(default=None, ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    available: bool
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_availability(self) -> "SubScore":
        if self.available != (self.value is not None):
            raise ValueError("available must match whether value is present")
        return self


class CapitalFlowSymbolSnapshot(CapitalFlowModel):
    snapshot_id: str
    symbol: str
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    volume_score: SubScore
    amount_score: SubScore
    turnover_score: SubScore
    price_volume_score: SubScore
    financing_score: SubScore
    sector_flow_score: SubScore
    liquidity_score: SubScore
    volume_ratio_5d: float | None = None
    volume_ratio_20d: float | None = None
    volume_percentile_20d: float | None = Field(default=None, ge=0, le=1)
    volume_percentile_60d: float | None = Field(default=None, ge=0, le=1)
    consecutive_volume_expansion_days: int = Field(default=0, ge=0)
    consecutive_volume_contraction_days: int = Field(default=0, ge=0)
    amount_ratio_5d: float | None = None
    amount_ratio_20d: float | None = None
    amount_percentile_20d: float | None = Field(default=None, ge=0, le=1)
    amount_percentile_60d: float | None = Field(default=None, ge=0, le=1)
    amount_market_rank: int | None = Field(default=None, ge=1)
    amount_market_percentile: float | None = Field(default=None, ge=0, le=1)
    turnover_rate: float | None = Field(default=None, ge=0)
    turnover_percentile_20d: float | None = Field(default=None, ge=0, le=1)
    turnover_percentile_60d: float | None = Field(default=None, ge=0, le=1)
    turnover_change: float | None = None
    consecutive_high_turnover_days: int = Field(default=0, ge=0)
    price_volume_state: PriceVolumeState
    average_amount_5d: float | None = Field(default=None, ge=0)
    average_amount_20d: float | None = Field(default=None, ge=0)
    zero_volume_days: int = Field(default=0, ge=0)
    suspended_or_illiquid: bool
    liquidity_level: LiquidityLevel
    liquidity_confidence: float = Field(ge=0, le=1)
    financing_balance: float | None = Field(default=None, ge=0)
    financing_balance_change_1d: float | None = None
    financing_balance_change_5d: float | None = None
    financing_buy_amount: float | None = Field(default=None, ge=0)
    securities_lending_balance: float | None = Field(default=None, ge=0)
    financing_trend: FinancingTrend
    estimated_flow: dict[str, Any] | None = None
    sector: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[CapitalFlowRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str
    shadow_mode: bool = True
    sample_universe_size: int = Field(default=1, ge=0)
    partial_universe: bool = True
    generated_at: datetime

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_shadow(self) -> "CapitalFlowSymbolSnapshot":
        if not self.shadow_mode:
            raise ValueError("capital-flow snapshots must remain shadow")
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        return self

    @property
    def sub_scores(self) -> dict[str, SubScore]:
        return {
            "volume": self.volume_score,
            "amount": self.amount_score,
            "turnover": self.turnover_score,
            "price_volume": self.price_volume_score,
            "financing": self.financing_score,
            "sector_flow": self.sector_flow_score,
            "liquidity": self.liquidity_score,
        }


class CapitalFlowSectorSnapshot(CapitalFlowModel):
    snapshot_id: str
    sector: str
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    sector_total_amount: float | None = Field(default=None, ge=0)
    sector_amount_market_share: float | None = Field(default=None, ge=0, le=1)
    sector_amount_ratio_20d: float | None = None
    sector_up_amount_share: float | None = Field(default=None, ge=0, le=1)
    sector_down_amount_share: float | None = Field(default=None, ge=0, le=1)
    active_symbol_ratio: float | None = Field(default=None, ge=0, le=1)
    sector_flow_breadth: float | None = Field(default=None, ge=-1, le=1)
    sector_concentration: float | None = Field(default=None, ge=0, le=1)
    leading_symbols: list[str] = Field(default_factory=list)
    mapping_version: str
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[CapitalFlowRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str
    shadow_mode: bool = True
    sample_universe_size: int = Field(ge=0)
    partial_universe: bool = True
    generated_at: datetime


class CapitalFlowMarketSnapshot(CapitalFlowModel):
    snapshot_id: str
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    market_total_amount: float | None = Field(default=None, ge=0)
    market_amount_ratio_20d: float | None = None
    market_active_symbol_count: int = Field(default=0, ge=0)
    high_volume_symbol_count: int = Field(default=0, ge=0)
    high_turnover_symbol_count: int = Field(default=0, ge=0)
    amount_concentration_top10: float | None = Field(default=None, ge=0, le=1)
    amount_concentration_top50: float | None = Field(default=None, ge=0, le=1)
    rising_amount_share: float | None = Field(default=None, ge=0, le=1)
    falling_amount_share: float | None = Field(default=None, ge=0, le=1)
    market_liquidity_temperature: str
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[CapitalFlowRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str
    shadow_mode: bool = True
    sample_universe_size: int = Field(ge=0)
    partial_universe: bool = True
    generated_at: datetime


class CapitalFlowCandidate(CapitalFlowModel):
    symbol: str
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    snapshot_id: str
    missing_fields: list[str]
    risk_flags: list[CapitalFlowRiskFlag]
    evidence_ids: list[str] = Field(default_factory=list)


class CapitalFlowAnalyzeRequest(CapitalFlowModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    scope: CapitalFlowScope = CapitalFlowScope.SYMBOL
    symbol: str | None = None
    symbols: list[str] = Field(default_factory=list, max_length=5000)
    sector: str | None = None
    persist: bool | None = None

    @field_validator("data_cutoff")
    @classmethod
    def require_cutoff_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> "CapitalFlowAnalyzeRequest":
        if self.scope == CapitalFlowScope.SYMBOL and not (self.symbol or self.symbols):
            raise ValueError("symbol or symbols is required for SYMBOL scope")
        if self.scope == CapitalFlowScope.SECTOR and not self.sector:
            raise ValueError("sector is required for SECTOR scope")
        return self


class CapitalFlowAnalyzeResponse(CapitalFlowModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    shadow_mode: bool = True
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    sub_scores: dict[str, SubScore] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[CapitalFlowRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    sample_universe_size: int = Field(ge=0)
    partial_universe: bool
    result: CapitalFlowSymbolSnapshot | CapitalFlowSectorSnapshot | CapitalFlowMarketSnapshot | None = None
    candidates: list[CapitalFlowCandidate] = Field(default_factory=list)


class CapitalFlowReadResponse(CapitalFlowAnalyzeResponse):
    pass


class CapitalFlowEvaluationRequest(CapitalFlowModel):
    snapshot_id: str
    data_cutoff: datetime

    @field_validator("data_cutoff")
    @classmethod
    def require_evaluation_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class CapitalFlowEvaluation(CapitalFlowModel):
    evaluation_id: str
    snapshot_id: str
    snapshot_time: datetime
    symbol: str
    data_cutoff: datetime
    capital_score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    price_volume_state: PriceVolumeState
    volume_ratio_20d: float | None = None
    turnover_rate: float | None = None
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    max_rise: float | None = None
    max_drawdown: float | None = None
    was_limit_up: bool | None = None
    was_limit_down: bool | None = None
    was_suspended: bool | None = None
    data_complete: bool
    evidence_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    generated_at: datetime
    algorithm_version: str


__all__ = [
    "CapitalFlowAnalyzeRequest",
    "CapitalFlowAnalyzeResponse",
    "CapitalFlowCandidate",
    "CapitalFlowEvaluation",
    "CapitalFlowEvaluationRequest",
    "CapitalFlowMarketSnapshot",
    "CapitalFlowReadResponse",
    "CapitalFlowRiskFlag",
    "CapitalFlowScope",
    "CapitalFlowSectorSnapshot",
    "CapitalFlowSymbolSnapshot",
    "FinancingTrend",
    "LiquidityLevel",
    "PriceVolumeState",
    "SubScore",
]
