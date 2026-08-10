from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from router.schemas import RouterInvokeResponse
from trading.research.sentiment.schemas import (
    ModelSentimentExtraction,
    SchemaValidationStatus,
    SentimentRiskFlag,
)


class SentimentInternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventSourceRecord(SentimentInternalModel):
    record_id: str
    event_time: datetime
    fetched_at: datetime
    source_name: str
    source_url: str | None = None
    source_level: str
    verified: bool
    content_hash: str | None = None
    payload: dict[str, Any]


class EventBundle(SentimentInternalModel):
    event_cluster_id: str
    canonical_title: str
    cluster_event_type: str
    event_time: datetime
    data_cutoff: datetime
    primary_source_id: str
    source_count: int
    source_records: list[EventSourceRecord]
    symbols: list[str]
    sectors: list[str]
    dedup_method: str
    dedup_version: str
    cluster_hash: str

    @property
    def primary_source(self) -> EventSourceRecord:
        for source in self.source_records:
            if source.record_id == self.primary_source_id:
                return source
        raise ValueError(
            f"primary source missing for event {self.event_cluster_id}"
        )


class ExtractionResult(SentimentInternalModel):
    extraction: ModelSentimentExtraction
    model_call_ids: list[str] = Field(default_factory=list)
    validation_status: SchemaValidationStatus
    risk_flags: list[SentimentRiskFlag] = Field(default_factory=list)
    used_model: bool = False
    provider: str | None = None


class SentimentModelProvider(Protocol):
    provider_name: str

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse: ...


__all__ = [
    "EventBundle",
    "EventSourceRecord",
    "ExtractionResult",
    "SentimentModelProvider",
]
