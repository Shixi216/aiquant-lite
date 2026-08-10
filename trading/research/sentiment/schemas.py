from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from data_hub.schemas.unified import FactorOutput
from trading.schemas import AnalysisMode


class SentimentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SentimentEventType(StrEnum):
    EARNINGS_POSITIVE = "EARNINGS_POSITIVE"
    EARNINGS_WARNING = "EARNINGS_WARNING"
    DIVIDEND = "DIVIDEND"
    SHARE_REPURCHASE = "SHARE_REPURCHASE"
    SHAREHOLDER_REDUCTION = "SHAREHOLDER_REDUCTION"
    SHAREHOLDER_INCREASE = "SHAREHOLDER_INCREASE"
    CONTRACT_WIN = "CONTRACT_WIN"
    MAJOR_LITIGATION = "MAJOR_LITIGATION"
    REGULATORY_PENALTY = "REGULATORY_PENALTY"
    INVESTIGATION = "INVESTIGATION"
    TRADING_SUSPENSION = "TRADING_SUSPENSION"
    TRADING_RESUMPTION = "TRADING_RESUMPTION"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    PRODUCT_PRICE_CHANGE = "PRODUCT_PRICE_CHANGE"
    INDUSTRY_NEWS = "INDUSTRY_NEWS"
    MARKET_RUMOR = "MARKET_RUMOR"
    OTHER = "OTHER"


class ImpactHorizon(StrEnum):
    INTRADAY = "INTRADAY"
    ONE_DAY = "1D"
    ONE_TO_FIVE_DAYS = "1_5D"
    ONE_TO_FOUR_WEEKS = "1_4W"
    LONG_TERM = "LONG_TERM"
    UNKNOWN = "UNKNOWN"


class FactType(StrEnum):
    OFFICIAL_FACT = "OFFICIAL_FACT"
    VERIFIED_FACT = "VERIFIED_FACT"
    MEDIA_STATEMENT = "MEDIA_STATEMENT"
    ANALYST_OPINION = "ANALYST_OPINION"
    SPECULATION = "SPECULATION"
    UNKNOWN = "UNKNOWN"


class SentimentVerificationStatus(StrEnum):
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    VERIFIED_MULTI_SOURCE = "VERIFIED_MULTI_SOURCE"
    SINGLE_OFFICIAL_SOURCE = "SINGLE_OFFICIAL_SOURCE"
    SINGLE_MEDIA_SOURCE = "SINGLE_MEDIA_SOURCE"
    CONFLICT = "CONFLICT"
    UNVERIFIED = "UNVERIFIED"
    RETRACTED = "RETRACTED"


class SentimentRiskFlag(StrEnum):
    DATA_GAP = "DATA_GAP"
    PARTIAL_UNIVERSE = "PARTIAL_UNIVERSE"
    EVENT_TIME_MISSING = "EVENT_TIME_MISSING"
    SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
    SINGLE_MEDIA_SOURCE = "SINGLE_MEDIA_SOURCE"
    EVENT_CONFLICT = "EVENT_CONFLICT"
    UNVERIFIED_EVENT = "UNVERIFIED_EVENT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    HIGH_RUMOR_RATIO = "HIGH_RUMOR_RATIO"
    STALE_SENTIMENT = "STALE_SENTIMENT"
    SYMBOL_RELEVANCE_LOW = "SYMBOL_RELEVANCE_LOW"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    RETRACTED_EVENT = "RETRACTED_EVENT"
    MODE_RESTRICTION = "MODE_RESTRICTION"


class SchemaValidationStatus(StrEnum):
    VALID = "VALID"
    REPAIRED = "REPAIRED"
    INVALID = "INVALID"
    NOT_CALLED = "NOT_CALLED"


_TRADE_INSTRUCTION = re.compile(
    r"(?i)(?:\b(?:BUY|SELL|HOLD|SUBMIT_ORDER|PLACE_ORDER)\b|"
    r"建议\s*(?:买入|卖出)|仓位(?:比例)?|止损(?:价)?|止盈(?:价)?|"
    r"目标价|收益保证)"
)


def contains_trade_instruction(value: str) -> bool:
    return bool(_TRADE_INSTRUCTION.search(value))


class ModelSentimentExtraction(SentimentModel):
    event_type: SentimentEventType
    direction: Literal[-1, 0, 1]
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    impact_horizon: ImpactHorizon
    fact_type: FactType
    affected_symbols: list[str] = Field(default_factory=list, max_length=50)
    affected_sectors: list[str] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary")
    @classmethod
    def reject_trade_instructions(cls, value: str) -> str:
        if contains_trade_instruction(value):
            raise ValueError("model output must not contain trade instructions")
        return value


class SentimentEventAnalysis(SentimentModel):
    sentiment_analysis_id: str = Field(min_length=1)
    event_cluster_id: str = Field(min_length=1)
    event_type: SentimentEventType
    direction: Literal[-1, 0, 1]
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    model_confidence: float = Field(ge=0, le=1)
    impact_horizon: ImpactHorizon
    fact_type: FactType
    affected_symbols: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    relevance_by_symbol: dict[str, float] = Field(default_factory=dict)
    summary: str = Field(min_length=1, max_length=1000)
    source_level: str = Field(min_length=1)
    source_quality: float = Field(ge=0, le=1)
    freshness_weight: float = Field(ge=0, le=1)
    verification_status: SentimentVerificationStatus
    verification_weight: float = Field(ge=0, le=1)
    relevance_weight: float = Field(ge=0, le=1)
    event_score: float = Field(ge=-1, le=1)
    propagation_heat: float = Field(ge=0)
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    model_call_ids: list[str] = Field(default_factory=list)
    extractor_version: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    data_cutoff: datetime
    shadow_mode: Literal[True] = True

    @field_validator("generated_at", "data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("summary")
    @classmethod
    def reject_trade_instructions(cls, value: str) -> str:
        if contains_trade_instruction(value):
            raise ValueError("sentiment summary must not contain trade instructions")
        return value

    @field_validator(
        "affected_symbols",
        "affected_sectors",
        "evidence_ids",
        "model_call_ids",
    )
    @classmethod
    def unique_lists(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_analysis(self) -> "SentimentEventAnalysis":
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        if self.direction == 0 and self.event_score != 0:
            raise ValueError("neutral events must have an event_score of zero")
        if self.verification_status in {
            SentimentVerificationStatus.CONFLICT,
            SentimentVerificationStatus.RETRACTED,
        } and self.event_score != 0:
            raise ValueError("conflicted or retracted events must score zero")
        if set(self.relevance_by_symbol) != set(self.affected_symbols):
            raise ValueError(
                "relevance_by_symbol must cover every affected symbol exactly"
            )
        return self


class SentimentSymbolSnapshot(SentimentModel):
    snapshot_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    generated_at: datetime
    event_count: int = Field(ge=0)
    positive_event_count: int = Field(ge=0)
    negative_event_count: int = Field(ge=0)
    neutral_event_count: int = Field(ge=0)
    weighted_event_score: float = Field(ge=-1, le=1)
    propagation_heat: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    top_positive_events: list[str] = Field(default_factory=list)
    top_negative_events: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    model_call_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str = Field(min_length=1)
    shadow_mode: Literal[True] = True

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class MarketBreadthSnapshot(SentimentModel):
    market_snapshot_id: str = Field(min_length=1)
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    generated_at: datetime
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    universe_size: int = Field(ge=0)
    advances: int | None = Field(default=None, ge=0)
    declines: int | None = Field(default=None, ge=0)
    flats: int | None = Field(default=None, ge=0)
    limit_ups: int | None = Field(default=None, ge=0)
    limit_downs: int | None = Field(default=None, ge=0)
    broken_limit_ups: int | None = Field(default=None, ge=0)
    broken_limit_up_rate: float | None = Field(default=None, ge=0, le=1)
    max_limit_up_streak: int | None = Field(default=None, ge=0)
    total_amount: float | None = Field(default=None, ge=0)
    amount_vs_20d_average: float | None = Field(default=None, ge=0)
    advance_amount_ratio: float | None = Field(default=None, ge=0, le=1)
    decline_amount_ratio: float | None = Field(default=None, ge=0, le=1)
    sector_advance_ratios: dict[str, float] = Field(default_factory=dict)
    sector_diffusion: float | None = Field(default=None, ge=0, le=1)
    high_level_strength: float | None = Field(default=None, ge=-1, le=1)
    temperature: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str = Field(min_length=1)
    shadow_mode: Literal[True] = True

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class SentimentEvaluation(SentimentModel):
    evaluation_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    event_time: datetime | None = None
    alert_or_snapshot_time: datetime
    data_cutoff: datetime
    sentiment_score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    max_rise: float | None = None
    max_drawdown: float | None = None
    was_suspended: bool | None = None
    was_limit_up: bool | None = None
    was_limit_down: bool | None = None
    data_complete: bool
    evidence_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    generated_at: datetime
    algorithm_version: str = Field(min_length=1)


class SentimentAnalyzeRequest(SentimentModel):
    analysis_mode: AnalysisMode = AnalysisMode.RESEARCH
    data_cutoff: datetime
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    event_cluster_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_model_calls: bool = False
    include_market_breadth: bool = True

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value

    @model_validator(mode="after")
    def enforce_screening_boundary(self) -> "SentimentAnalyzeRequest":
        if self.analysis_mode == AnalysisMode.SCREENING and self.allow_model_calls:
            raise ValueError("SCREENING does not allow model calls")
        return self


class SentimentAnalyzeResponse(SentimentModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    shadow_mode: Literal[True] = True
    analyses: list[SentimentEventAnalysis] = Field(default_factory=list)
    symbol_snapshot: SentimentSymbolSnapshot | None = None
    market_snapshot: MarketBreadthSnapshot | None = None
    factor_output: FactorOutput | None = None
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SentimentEvaluationRequest(SentimentModel):
    snapshot_id: str = Field(min_length=1)
    data_cutoff: datetime

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class SentimentReadResponse(SentimentModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    shadow_mode: Literal[True] = True
    result: SentimentEventAnalysis | SentimentSymbolSnapshot | MarketBreadthSnapshot
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


__all__ = [
    "FactType",
    "ImpactHorizon",
    "MarketBreadthSnapshot",
    "ModelSentimentExtraction",
    "SchemaValidationStatus",
    "SentimentAnalyzeRequest",
    "SentimentAnalyzeResponse",
    "SentimentEvaluation",
    "SentimentEvaluationRequest",
    "SentimentEventAnalysis",
    "SentimentEventType",
    "SentimentReadResponse",
    "SentimentRiskFlag",
    "SentimentSymbolSnapshot",
    "SentimentVerificationStatus",
    "contains_trade_instruction",
]
