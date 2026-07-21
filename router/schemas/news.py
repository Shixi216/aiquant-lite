from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


JsonScalar: TypeAlias = str | int | float | bool | None

SourceClass = Literal[
    "FACT",
    "MEDIA_STATEMENT",
    "SPECULATION",
]

SentimentDirection = Literal[
    "positive",
    "negative",
    "neutral",
    "mixed",
]

SignalType = Literal[
    "rating_change",
    "target_price",
    "earnings_forecast",
    "production",
    "legal_risk",
    "capital_action",
    "other",
]

DuplicateLevel = Literal[
    "none",
    "partial",
    "high",
]


class NewsFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: JsonScalar
    unit: str | None


class NewsSentiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: SentimentDirection
    strength: float = Field(ge=0, le=1)
    evidence: list[str]


class NewsMarketSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SignalType
    value: str = Field(min_length=1)
    attribution: str = Field(min_length=1)


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_time: str | None
    subjects: list[str]
    event: str = Field(min_length=1)
    source_class: SourceClass
    source_name: str | None
    facts: list[NewsFact]
    sentiment: NewsSentiment
    market_signals: list[NewsMarketSignal]
    confidence: float = Field(ge=0, le=1)


class NewsCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    item_indexes: list[int]
    duplicate_level: DuplicateLevel
    incremental_information: list[str]


class NewsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    main_events: list[str]
    dominant_sentiment: SentimentDirection
    unverified_claims: list[str]


class NewsProcessorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NewsItem]
    clusters: list[NewsCluster]
    summary: NewsSummary