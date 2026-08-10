from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from data_hub.schemas.unified import FactorOutput, FactorType
from trading.schemas import Action, AnalysisMode


class OrchestrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactorAvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    MODE_RESTRICTED = "MODE_RESTRICTED"
    FUTURE_DATA_REJECTED = "FUTURE_DATA_REJECTED"
    UNVERIFIED = "UNVERIFIED"


class OrchestrationRiskFlag(StrEnum):
    FACTOR_DATA_GAP = "FACTOR_DATA_GAP"
    LOW_FACTOR_COVERAGE = "LOW_FACTOR_COVERAGE"
    SINGLE_FACTOR_DOMINANCE = "SINGLE_FACTOR_DOMINANCE"
    STALE_FACTOR = "STALE_FACTOR"
    FACTOR_CONFLICT = "FACTOR_CONFLICT"
    SHARED_EVIDENCE = "SHARED_EVIDENCE"
    HIGH_FACTOR_CORRELATION = "HIGH_FACTOR_CORRELATION"
    FUTURE_FACTOR_REJECTED = "FUTURE_FACTOR_REJECTED"
    UNVERIFIED_FACTOR = "UNVERIFIED_FACTOR"
    MODE_RESTRICTION = "MODE_RESTRICTION"
    COMPOSITE_CONFIDENCE_LOW = "COMPOSITE_CONFIDENCE_LOW"
    FORMAL_WEIGHT_ZERO = "FORMAL_WEIGHT_ZERO"


class FactorView(OrchestrationModel):
    factor_output_id: str = Field(min_length=1)
    factor_type: FactorType
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    shadow_mode: bool
    generated_at: datetime
    data_cutoff: datetime
    evidence_ids: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    algorithm_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    availability_status: FactorAvailabilityStatus
    availability_reason: str
    event_cluster_ids: list[str] = Field(default_factory=list)
    market_record_ids: list[str] = Field(default_factory=list)
    source_factor_output_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generated_at", "data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class FactorCoverage(OrchestrationModel):
    available_count: int = Field(ge=0, le=5)
    total_count: Literal[5] = 5
    coverage_ratio: float = Field(ge=0, le=1)
    display: str = Field(pattern=r"^[0-5]/5$")


class SharedEvidenceGroup(OrchestrationModel):
    group_id: str
    evidence_kind: Literal["EVENT_CLUSTER", "DATA_RECORD", "MARKET_RECORD"]
    evidence_ids: list[str] = Field(min_length=1)
    factor_types: list[FactorType] = Field(min_length=2)
    algorithm_version: str


class CorrelationDiscount(OrchestrationModel):
    factor_pair: str
    overlap_ratio: float = Field(ge=0, le=1)
    applied_discount: float = Field(ge=0, le=1)
    discounted_factor: FactorType
    shared_evidence_ids: list[str] = Field(default_factory=list)
    shared_event_cluster_ids: list[str] = Field(default_factory=list)
    source_factor_output_ids: list[str] = Field(default_factory=list)
    algorithm_version: str
    explanation: str


class FactorBundle(OrchestrationModel):
    symbol: str
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    technical: FactorView | None = None
    fundamental: FactorView | None = None
    sentiment: FactorView | None = None
    policy_news: FactorView | None = None
    capital_flow: FactorView | None = None
    available_factor_types: list[FactorType] = Field(default_factory=list)
    missing_factor_types: list[FactorType] = Field(default_factory=list)
    stale_factor_types: list[FactorType] = Field(default_factory=list)
    conflicting_factor_types: list[FactorType] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    shared_event_cluster_ids: list[str] = Field(default_factory=list)
    shared_evidence_groups: list[SharedEvidenceGroup] = Field(
        default_factory=list
    )
    risk_flags: list[OrchestrationRiskFlag] = Field(default_factory=list)
    factor_coverage: FactorCoverage
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value

    def factor_map(self) -> dict[FactorType, FactorView]:
        values = {
            FactorType.TECHNICAL: self.technical,
            FactorType.FUNDAMENTAL: self.fundamental,
            FactorType.SENTIMENT: self.sentiment,
            FactorType.POLICY_NEWS: self.policy_news,
            FactorType.CAPITAL_FLOW: self.capital_flow,
        }
        return {key: value for key, value in values.items() if value is not None}


class ShadowComposite(OrchestrationModel):
    factor_output: FactorOutput
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    shadow_mode: Literal[True] = True
    formal_strategy_weight: Literal[0.0] = 0.0
    configured_weights: dict[str, float]
    effective_weights: dict[str, float]
    factor_contributions: dict[str, float]
    correlation_discounts: list[CorrelationDiscount]
    source_factor_output_ids: list[str]
    shared_evidence_groups: list[SharedEvidenceGroup]
    missing_factor_types: list[FactorType]
    stale_factor_types: list[FactorType]
    risk_flags: list[OrchestrationRiskFlag]
    algorithm_version: str
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FormalResult(OrchestrationModel):
    strategy_version: str
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    proposal_action: Action
    final_action: Action
    hard_veto: bool
    formal_weights: dict[str, float]
    shadow_used_for_action: Literal[False] = False
    shadow_used_for_veto: Literal[False] = False


class ScreeningRequest(OrchestrationModel):
    data_cutoff: datetime
    symbols: list[str] | None = None
    limit: int = Field(default=20, ge=10, le=30)

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class ScreeningCandidate(OrchestrationModel):
    symbol: str
    shadow_score: float = Field(ge=-1, le=1)
    composite_confidence: float = Field(ge=0, le=1)
    factor_coverage: FactorCoverage
    available_factors: list[FactorType]
    missing_factors: list[FactorType]
    top_positive_factors: list[FactorType]
    top_negative_factors: list[FactorType]
    risk_flags: list[OrchestrationRiskFlag]


class ScreeningPerformance(OrchestrationModel):
    symbol_count: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    database_connection_count: int = Field(ge=0)
    network_request_count: Literal[0] = 0
    model_call_count: Literal[0] = 0
    decision_packet_count: Literal[0] = 0


class ScreeningResponse(OrchestrationModel):
    analysis_mode: Literal[AnalysisMode.SCREENING] = AnalysisMode.SCREENING
    formal_result: None = None
    candidates: list[ScreeningCandidate]
    coverage_distribution: dict[str, int]
    performance: ScreeningPerformance
    data_cutoff: datetime


class ResearchRequest(OrchestrationModel):
    symbols: list[str] = Field(min_length=1, max_length=30)
    data_cutoff: datetime
    persist: bool = True
    allow_external_fetch: bool = False

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class ResearchResult(OrchestrationModel):
    symbol: str
    analysis_mode: Literal[AnalysisMode.RESEARCH] = AnalysisMode.RESEARCH
    formal_result: None = None
    shadow_composite: ShadowComposite
    factor_coverage: FactorCoverage
    available_factors: list[FactorType]
    missing_factors: list[FactorType]
    suggested_capture_items: list[FactorType]
    evidence_ids: list[str]
    risk_flags: list[OrchestrationRiskFlag]
    persisted: bool


class ResearchResponse(OrchestrationModel):
    analysis_mode: Literal[AnalysisMode.RESEARCH] = AnalysisMode.RESEARCH
    data_cutoff: datetime
    results: list[ResearchResult]
    network_request_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)


class DecisionShadowRequest(OrchestrationModel):
    symbol: str
    data_cutoff: datetime
    technical_score: float = Field(ge=-1, le=1)
    technical_confidence: float = Field(ge=0, le=1)
    fundamental_score: float = Field(ge=-1, le=1)
    fundamental_confidence: float = Field(ge=0, le=1)
    hard_veto: bool = False
    persist_shadow: bool = True

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class DecisionShadowResponse(OrchestrationModel):
    analysis_mode: Literal[AnalysisMode.DECISION] = AnalysisMode.DECISION
    formal_result: FormalResult
    shadow_composite: ShadowComposite
    formal_weights: dict[str, float]
    effective_shadow_weights: dict[str, float]
    factor_coverage: FactorCoverage
    available_factors: list[FactorType]
    missing_factors: list[FactorType]
    correlation_discounts: list[CorrelationDiscount]
    evidence_ids: list[str]
    risk_flags: list[OrchestrationRiskFlag]
    decision_packet_created: Literal[False] = False


class SymbolOrchestrationResponse(OrchestrationModel):
    analysis_mode: AnalysisMode
    bundle: FactorBundle
    shadow_composite: ShadowComposite
    formal_result: None = None


class EvaluationRequest(OrchestrationModel):
    symbol: str
    analysis_time: datetime
    formal_score: float = Field(ge=-1, le=1)
    formal_action: Action
    shadow_score: float = Field(ge=-1, le=1)
    composite_confidence: float = Field(ge=0, le=1)
    factor_coverage: FactorCoverage
    effective_weights: dict[str, float]
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    maximum_upside: float | None = None
    maximum_drawdown: float | None = None
    risk_vetoed: bool
    data_complete: bool
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_evaluation_order(self) -> "EvaluationRequest":
        if self.evaluated_at <= self.analysis_time:
            raise ValueError("evaluation must occur after analysis_time")
        return self


class EvaluationResponse(OrchestrationModel):
    evaluation_id: str
    persisted: bool
    score_mutated: Literal[False] = False


__all__ = [
    "CorrelationDiscount",
    "DecisionShadowRequest",
    "DecisionShadowResponse",
    "EvaluationRequest",
    "EvaluationResponse",
    "FactorAvailabilityStatus",
    "FactorBundle",
    "FactorCoverage",
    "FactorView",
    "FormalResult",
    "OrchestrationRiskFlag",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchResult",
    "ScreeningCandidate",
    "ScreeningPerformance",
    "ScreeningRequest",
    "ScreeningResponse",
    "ShadowComposite",
    "SharedEvidenceGroup",
    "SymbolOrchestrationResponse",
]
