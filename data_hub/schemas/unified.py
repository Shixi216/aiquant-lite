from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"
    SINGLE_SOURCE = "SINGLE_SOURCE"


class FactorType(StrEnum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    SENTIMENT = "SENTIMENT"
    POLICY_NEWS = "POLICY_NEWS"
    CAPITAL_FLOW = "CAPITAL_FLOW"
    SHADOW_COMPOSITE = "SHADOW_COMPOSITE"


class UnifiedDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalRecord(UnifiedDataModel):
    canonical_record_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    event_time: datetime
    data_cutoff: datetime
    generated_at: datetime
    primary_source: str = Field(min_length=1)
    source_record_ids: list[str] = Field(min_length=1)
    verification_source_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    field_differences: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str = Field(min_length=1)

    @field_validator("event_time", "data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> "CanonicalRecord":
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("source_record_ids must be unique")
        if len(self.verification_source_ids) != len(
            set(self.verification_source_ids)
        ):
            raise ValueError("verification_source_ids must be unique")
        if not set(self.verification_source_ids) <= set(self.source_record_ids):
            raise ValueError(
                "verification_source_ids must reference source_record_ids"
            )
        return self


class CanonicalMarketRecord(CanonicalRecord):
    pass


class CanonicalFinancialRecord(CanonicalRecord):
    pass


class EventCluster(UnifiedDataModel):
    event_cluster_id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    event_time: datetime
    data_cutoff: datetime
    primary_source_id: str = Field(min_length=1)
    source_count: int = Field(ge=1)
    source_record_ids: list[str] = Field(min_length=1)
    symbol_links: list[str] = Field(default_factory=list)
    sector_links: list[str] = Field(default_factory=list)
    dedup_method: str = Field(min_length=1)
    dedup_version: str = Field(min_length=1)
    cluster_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime

    @field_validator("event_time", "data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_cluster(self) -> "EventCluster":
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        if self.source_count != len(self.source_record_ids):
            raise ValueError("source_count must equal unique source records")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("source_record_ids must be unique")
        if self.primary_source_id not in self.source_record_ids:
            raise ValueError("primary_source_id must reference a source record")
        if len(self.symbol_links) != len(set(self.symbol_links)):
            raise ValueError("symbol_links must be unique")
        if len(self.sector_links) != len(set(self.sector_links)):
            raise ValueError("sector_links must be unique")
        return self


_HIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_chain_of_thought",
    "hidden_reasoning",
    "cot",
}


def _contains_hidden_reasoning(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().casefold() in _HIDDEN_REASONING_KEYS:
                return True
            if _contains_hidden_reasoning(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_hidden_reasoning(item) for item in value)
    return False


class FactorOutput(UnifiedDataModel):
    factor_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    symbol: str = Field(min_length=1)
    factor_type: FactorType
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    data_cutoff: datetime
    generated_at: datetime
    evidence_ids: list[str] = Field(min_length=1)
    risk_flags: list[str] = Field(default_factory=list)
    model_call_ids: list[str] = Field(default_factory=list)
    algorithm_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_mode: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_factor(self) -> "FactorOutput":
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if len(self.model_call_ids) != len(set(self.model_call_ids)):
            raise ValueError("model_call_ids must be unique")
        if _contains_hidden_reasoning(self.metadata):
            raise ValueError("metadata must not contain hidden chain-of-thought")
        return self


__all__ = [
    "CanonicalFinancialRecord",
    "CanonicalMarketRecord",
    "EventCluster",
    "FactorOutput",
    "FactorType",
    "VerificationStatus",
]
