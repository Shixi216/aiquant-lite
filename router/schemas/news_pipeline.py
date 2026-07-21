from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from router.schemas.news import NewsProcessorOutput


NewsDataType = Literal[
    "finance_news",
    "announcement",
]

MetadataSourceClass = Literal[
    "FACT",
    "MEDIA_STATEMENT",
    "SPECULATION",
]


class NewsBundleRecord(BaseModel):
    """Normalized source record sent to news_processor."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    symbol: str
    data_type: NewsDataType
    event_time: str | None
    source_name: str | None
    source_url: str | None
    source_level: str
    verified: bool
    metadata_class: MetadataSourceClass
    content_hash: str | None
    title: str
    content: str | None
    published_at: str | None


class NewsSourceBundle(BaseModel):
    """Compact bundle built from Data Hub sources."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start_date: str
    end_date: str
    finance_news_count: int = Field(ge=0)
    announcement_count: int = Field(ge=0)
    records: list[NewsBundleRecord]


class NewsPipelineRequest(BaseModel):
    """Request for the complete news analysis pipeline."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(
        min_length=1,
        max_length=32,
    )
    start_date: date
    end_date: date
    finance_news_limit: int = Field(
        default=5,
        ge=1,
        le=20,
    )
    announcement_limit: int = Field(
        default=5,
        ge=1,
        le=20,
    )
    temperature: float = Field(
        default=0,
        ge=0,
        le=1,
    )
    max_tokens: int = Field(
        default=6000,
        ge=512,
        le=8192,
    )

    @model_validator(mode="after")
    def validate_date_range(
        self,
    ) -> NewsPipelineRequest:
        if self.start_date > self.end_date:
            raise ValueError(
                "start_date 不能晚于 end_date"
            )

        return self


class NewsPipelineResponse(BaseModel):
    """Result from the complete news analysis pipeline."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start_date: date
    end_date: date
    finance_news_count: int
    announcement_count: int
    source_record_count: int
    selected_record_ids: list[str]
    task_id: str
    call_ids: list[str]
    attempts: int
    validated: bool
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, int] = Field(default_factory=dict)
    output: NewsProcessorOutput