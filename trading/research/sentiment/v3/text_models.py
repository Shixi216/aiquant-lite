from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from trading.research.sentiment.v3.schemas import (
    QualityFlag,
    RecordSource,
    SentimentSnapshotMeta,
    SnapshotModel,
    SnapshotType,
)

TEXT_ALGORITHM_VERSION = "sentiment-v3-text-s2d-1.0.0"
NEWS_SNAPSHOT_ALGORITHM_VERSION = "sentiment-v3-news-s2d-1.0.0"
EVENT_ALGORITHM_VERSION = "sentiment-v3-event-s2d-1.0.0"
REACTION_ALGORITHM_VERSION = "sentiment-v3-reaction-s2d-1.0.0"
TEXT_PROMPT_VERSION = "TEXT_INTELLIGENCE_PROMPT_V1"
NEWS_SENTIMENT_PROMPT_VERSION = "NEWS_SENTIMENT_PROMPT_V1"
EVENT_INTELLIGENCE_PROMPT_VERSION = "EVENT_INTELLIGENCE_PROMPT_V1"
CANONICAL_EVENT_VERSION = "CANONICAL_EVENT_RULE_V1"
REACTION_CLASSIFICATION_VERSION = "REACTION_SIGN_V1"


class TextType(StrEnum):
    FACT_EVENT = "FACT_EVENT"
    EXPECTATION_CHANGE = "EXPECTATION_CHANGE"
    MARKET_REACTION = "MARKET_REACTION"
    OPINION = "OPINION"
    RISK = "RISK"
    RUMOR = "RUMOR"
    OTHER = "OTHER"


class EventType(StrEnum):
    EARNINGS = "EARNINGS"
    EARNINGS_FORECAST = "EARNINGS_FORECAST"
    DIVIDEND = "DIVIDEND"
    BUYBACK = "BUYBACK"
    SHAREHOLDER_INCREASE = "SHAREHOLDER_INCREASE"
    SHAREHOLDER_REDUCTION = "SHAREHOLDER_REDUCTION"
    CONTRACT = "CONTRACT"
    ORDER = "ORDER"
    M_AND_A = "M_AND_A"
    ASSET_RESTRUCTURING = "ASSET_RESTRUCTURING"
    FINANCING = "FINANCING"
    EQUITY_INCENTIVE = "EQUITY_INCENTIVE"
    LITIGATION = "LITIGATION"
    PENALTY = "PENALTY"
    REGULATORY = "REGULATORY"
    INVESTIGATION = "INVESTIGATION"
    ST_STATUS = "ST_STATUS"
    DELISTING_RISK = "DELISTING_RISK"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    MAJOR_SHAREHOLDER_CHANGE = "MAJOR_SHAREHOLDER_CHANGE"
    PRODUCT = "PRODUCT"
    CAPACITY = "CAPACITY"
    POLICY = "POLICY"
    OTHER = "OTHER"


class TextDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class TextHorizon(StrEnum):
    INTRADAY = "INTRADAY"
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"
    UNKNOWN = "UNKNOWN"


class Novelty(StrEnum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    FOLLOW_UP = "FOLLOW_UP"
    DUPLICATE = "DUPLICATE"
    UNKNOWN = "UNKNOWN"


class FactStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REPORTED = "REPORTED"
    UNVERIFIED = "UNVERIFIED"
    RUMOR = "RUMOR"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


class TextSourceQuality(StrEnum):
    OFFICIAL_DISCLOSURE = "OFFICIAL_DISCLOSURE"
    OFFICIAL_REGULATOR = "OFFICIAL_REGULATOR"
    COMPANY_SOURCE = "COMPANY_SOURCE"
    MAINSTREAM_FINANCIAL_MEDIA = "MAINSTREAM_FINANCIAL_MEDIA"
    OTHER_MEDIA = "OTHER_MEDIA"
    UNKNOWN = "UNKNOWN"


class ExtractionMethod(StrEnum):
    STRUCTURED_AI = "STRUCTURED_AI"
    RULE_FALLBACK = "RULE_FALLBACK"


class EventSession(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    IN_SESSION = "IN_SESSION"
    POST_MARKET = "POST_MARKET"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    UNKNOWN = "UNKNOWN"


class ReactionWindowType(StrEnum):
    OPEN_REACTION = "OPEN_REACTION"
    INTRADAY_REACTION = "INTRADAY_REACTION"
    EOD_REACTION = "EOD_REACTION"
    NEXT_SESSION_REACTION = "NEXT_SESSION_REACTION"
    MULTI_DAY_REACTION = "MULTI_DAY_REACTION"


class ReactionState(StrEnum):
    CONFIRMED_POSITIVE = "CONFIRMED_POSITIVE"
    POSITIVE_EVENT_REJECTED = "POSITIVE_EVENT_REJECTED"
    NEGATIVE_EVENT_ABSORBED = "NEGATIVE_EVENT_ABSORBED"
    CONFIRMED_NEGATIVE = "CONFIRMED_NEGATIVE"
    MIXED_REACTION = "MIXED_REACTION"
    NO_CLEAR_REACTION = "NO_CLEAR_REACTION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TextDocument(SnapshotModel):
    document_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    title: str = Field(min_length=1, max_length=2000)
    body: str = Field(default="", max_length=100_000)
    source: str = Field(min_length=1)
    source_url: str | None = None
    is_announcement: bool = False
    event_time: datetime | None = None
    published_time: datetime | None = None
    available_time: datetime
    ingested_time: datetime
    source_quality: TextSourceQuality
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_flags: list[QualityFlag] = Field(default_factory=list)

    @field_validator("event_time", "published_time", "available_time", "ingested_time")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("text timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> "TextDocument":
        if self.available_time > self.ingested_time:
            raise ValueError("available_time cannot be later than ingested_time")
        if self.published_time is not None and self.published_time > self.available_time:
            raise ValueError("published_time cannot be later than available_time")
        return self


class TextExtractionOutput(SnapshotModel):
    text_type: TextType
    event_type: EventType
    direction: TextDirection
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: TextHorizon
    novelty: Novelty
    surprise: TextDirection | None = None
    surprise_basis: str | None = Field(default=None, max_length=500)
    event_time: datetime | None = None
    available_time: datetime
    source_quality: TextSourceQuality
    fact_status: FactStatus
    evidence: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("event_time", "available_time")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("extraction timestamps must include a timezone")
        return value

    @field_validator("evidence")
    @classmethod
    def limit_evidence(cls, value: list[str]) -> list[str]:
        return [item.strip()[:240] for item in value if item.strip()][:8]

    @model_validator(mode="after")
    def surprise_requires_basis(self) -> "TextExtractionOutput":
        if self.surprise is not None and not self.surprise_basis:
            raise ValueError("surprise requires an explicit comparison basis")
        return self


class TextAnalysisRecord(SnapshotModel):
    analysis_id: str
    document_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    output: TextExtractionOutput
    extraction_method: ExtractionMethod
    provider: str
    model_name: str
    prompt_version: str
    schema_version: str
    algorithm_version: str
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    processed_at: datetime
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    quality_flags: list[QualityFlag] = Field(default_factory=list)

    @field_validator("processed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("processed_at must include a timezone")
        return value


class NewsSentimentRecord(SnapshotModel):
    record_id: str
    analysis_id: str
    document_id: str
    content_hash: str
    symbol: str
    text_type: TextType
    direction: TextDirection
    confidence: float = Field(ge=0, le=1)
    horizon: TextHorizon
    novelty: Novelty
    source_quality: TextSourceQuality
    available_time: datetime
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_news_type(self) -> "NewsSentimentRecord":
        if self.text_type not in {
            TextType.OPINION,
            TextType.EXPECTATION_CHANGE,
            TextType.MARKET_REACTION,
            TextType.RUMOR,
        }:
            raise ValueError("NewsSentimentRecord accepts only narrative text types")
        return self


class EventIntelligenceRecord(SnapshotModel):
    event_id: str
    canonical_event_id: str
    canonical_event_version: str = CANONICAL_EVENT_VERSION
    symbol: str
    event_type: EventType
    direction: TextDirection
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: TextHorizon
    novelty: Novelty
    surprise: TextDirection | None = None
    surprise_basis: str | None = None
    event_time: datetime | None = None
    published_time: datetime | None = None
    available_time: datetime
    event_session: EventSession
    source_quality: TextSourceQuality
    fact_status: FactStatus
    evidence: list[str] = Field(default_factory=list)
    source_document_hashes: list[str] = Field(min_length=1)
    official_confirmation_exists: bool = False
    risk_event_candidate: bool = False
    extraction_method: ExtractionMethod
    provider: str
    model_name: str
    prompt_version: str
    schema_version: str
    algorithm_version: str
    processed_at: datetime
    revision: int = Field(default=1, ge=1)
    supersedes_event_id: str | None = None
    record_source: RecordSource = RecordSource.REAL
    formal_weight: float = 0.0

    @field_validator("event_time", "published_time", "available_time", "processed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("event timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_revision(self) -> "EventIntelligenceRecord":
        if self.revision == 1 and self.supersedes_event_id is not None:
            raise ValueError("first revision cannot supersede an event")
        if self.revision > 1 and not self.supersedes_event_id:
            raise ValueError("later event revision requires supersedes_event_id")
        return self


class NewsSentimentSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    symbol: str
    window_start: datetime
    window_end: datetime
    document_count: int = Field(ge=0)
    unique_document_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    opinion_count: int = Field(ge=0)
    expectation_change_count: int = Field(ge=0)
    market_reaction_text_count: int = Field(ge=0)
    rumor_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    neutral_count: int = Field(ge=0)
    mixed_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    confidence_min: float | None = None
    confidence_median: float | None = None
    confidence_max: float | None = None
    novel_document_count: int = Field(ge=0)
    duplicate_document_count: int = Field(ge=0)
    source_quality_distribution: dict[str, int] = Field(default_factory=dict)
    latest_available_time: datetime | None = None
    attention_proxy_fields: list[str] = Field(
        default_factory=lambda: [
            "document_count",
            "unique_document_count",
            "source_count",
            "novel_document_count",
        ]
    )
    field_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type(self) -> "NewsSentimentSnapshot":
        if self.meta.snapshot_type != SnapshotType.NEWS:
            raise ValueError("news snapshot requires NEWS metadata")
        return self


class EventIntelligenceSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    event: EventIntelligenceRecord

    @model_validator(mode="after")
    def validate_type(self) -> "EventIntelligenceSnapshot":
        if self.meta.snapshot_type != SnapshotType.EVENT_INTELLIGENCE:
            raise ValueError("event snapshot requires EVENT_INTELLIGENCE metadata")
        return self


OptionalReturn = Annotated[float | None, Field(default=None)]


class EventReactionSnapshot(SnapshotModel):
    meta: SentimentSnapshotMeta
    event_id: str
    canonical_event_id: str
    event_direction: TextDirection
    event_available_time: datetime
    event_session: EventSession
    reaction_start: datetime
    reaction_end: datetime
    window_type: ReactionWindowType
    classification: ReactionState
    classification_version: str = REACTION_CLASSIFICATION_VERSION
    stock_return: OptionalReturn = None
    market_return: OptionalReturn = None
    sector_return: OptionalReturn = None
    relative_market_return: OptionalReturn = None
    relative_sector_return: OptionalReturn = None
    volume_change: OptionalReturn = None
    turnover_change: OptionalReturn = None
    close_location_value: OptionalReturn = None
    open_gap_pct: OptionalReturn = None
    high_to_close_drawdown: OptionalReturn = None
    low_to_close_recovery: OptionalReturn = None
    vendor_main_flow_ratio: OptionalReturn = None
    relative_market_flow: OptionalReturn = None
    relative_sector_flow: OptionalReturn = None
    market_snapshot_id: str | None = None
    sector_snapshot_id: str | None = None
    stock_snapshot_id: str | None = None
    capital_snapshot_id: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type_and_window(self) -> "EventReactionSnapshot":
        if self.meta.snapshot_type != SnapshotType.EVENT_REACTION:
            raise ValueError("reaction snapshot requires EVENT_REACTION metadata")
        if self.reaction_start < self.event_available_time:
            raise ValueError("reaction cannot start before event availability")
        if self.reaction_end < self.reaction_start:
            raise ValueError("reaction_end cannot precede reaction_start")
        return self


class LLMCallStats(SnapshotModel):
    documents_seen: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    cache_misses: int = Field(ge=0)
    qwen_calls: int = Field(ge=0)
    fallback_calls: int = Field(ge=0)
    qwen_failures: int = Field(ge=0)
    duplicate_suppressed: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    qwen_latency_ms: float = Field(ge=0)
    hash_ms: float = Field(ge=0)
    cache_lookup_ms: float = Field(ge=0)
    clustering_ms: float = Field(ge=0)


__all__ = [
    "CANONICAL_EVENT_VERSION",
    "EVENT_ALGORITHM_VERSION",
    "EVENT_INTELLIGENCE_PROMPT_VERSION",
    "EventIntelligenceRecord",
    "EventIntelligenceSnapshot",
    "EventSession",
    "EventReactionSnapshot",
    "EventType",
    "ExtractionMethod",
    "FactStatus",
    "LLMCallStats",
    "NEWS_SENTIMENT_PROMPT_VERSION",
    "NEWS_SNAPSHOT_ALGORITHM_VERSION",
    "NewsSentimentRecord",
    "NewsSentimentSnapshot",
    "Novelty",
    "REACTION_ALGORITHM_VERSION",
    "REACTION_CLASSIFICATION_VERSION",
    "ReactionState",
    "ReactionWindowType",
    "TEXT_ALGORITHM_VERSION",
    "TEXT_PROMPT_VERSION",
    "TextAnalysisRecord",
    "TextDirection",
    "TextDocument",
    "TextExtractionOutput",
    "TextHorizon",
    "TextSourceQuality",
    "TextType",
]
