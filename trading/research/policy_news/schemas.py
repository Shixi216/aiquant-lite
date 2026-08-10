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


class PolicyNewsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyEventCategory(StrEnum):
    INDUSTRIAL_SUPPORT = "INDUSTRIAL_SUPPORT"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    CORPORATE_MAJOR_EVENT = "CORPORATE_MAJOR_EVENT"
    OTHER = "OTHER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PolicyEventType(StrEnum):
    FISCAL_SUPPORT = "FISCAL_SUPPORT"
    TAX_INCENTIVE = "TAX_INCENTIVE"
    SUBSIDY = "SUBSIDY"
    MARKET_ACCESS = "MARKET_ACCESS"
    INDUSTRIAL_PLAN = "INDUSTRIAL_PLAN"
    TECHNICAL_STANDARD = "TECHNICAL_STANDARD"
    GOVERNMENT_PROCUREMENT = "GOVERNMENT_PROCUREMENT"
    PILOT_POLICY = "PILOT_POLICY"
    CAPACITY_POLICY = "CAPACITY_POLICY"
    REGULATORY_INVESTIGATION = "REGULATORY_INVESTIGATION"
    EXCHANGE_INQUIRY = "EXCHANGE_INQUIRY"
    ADMINISTRATIVE_PENALTY = "ADMINISTRATIVE_PENALTY"
    MARKET_BAN = "MARKET_BAN"
    FRAUD_INVESTIGATION = "FRAUD_INVESTIGATION"
    DISCLOSURE_VIOLATION = "DISCLOSURE_VIOLATION"
    SAFETY_ENVIRONMENTAL_PENALTY = "SAFETY_ENVIRONMENTAL_PENALTY"
    ANTITRUST_INVESTIGATION = "ANTITRUST_INVESTIGATION"
    REGULATORY_REMEDIATION = "REGULATORY_REMEDIATION"
    MAJOR_CONTRACT = "MAJOR_CONTRACT"
    ASSET_RESTRUCTURING = "ASSET_RESTRUCTURING"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    CONTROL_CHANGE = "CONTROL_CHANGE"
    EQUITY_TRANSFER = "EQUITY_TRANSFER"
    MAJOR_LITIGATION = "MAJOR_LITIGATION"
    MAJOR_GUARANTEE = "MAJOR_GUARANTEE"
    MAJOR_INVESTMENT = "MAJOR_INVESTMENT"
    CAPACITY_LAUNCH = "CAPACITY_LAUNCH"
    PRODUCT_APPROVAL = "PRODUCT_APPROVAL"
    CORE_PROJECT_TERMINATION = "CORE_PROJECT_TERMINATION"
    DEBT_DEFAULT = "DEBT_DEFAULT"
    BANKRUPTCY_REORGANIZATION = "BANKRUPTCY_REORGANIZATION"
    MATERIAL_SUSPENSION_REASON = "MATERIAL_SUSPENSION_REASON"
    OTHER = "OTHER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PolicyFactType(StrEnum):
    OFFICIAL_DOCUMENT = "OFFICIAL_DOCUMENT"
    COMPANY_ANNOUNCEMENT = "COMPANY_ANNOUNCEMENT"
    REGULATORY_DISCLOSURE = "REGULATORY_DISCLOSURE"
    VERIFIED_MEDIA_REPORT = "VERIFIED_MEDIA_REPORT"
    MEDIA_REPORT = "MEDIA_REPORT"
    ANALYST_INTERPRETATION = "ANALYST_INTERPRETATION"
    SPECULATION = "SPECULATION"
    MODEL_INFERENCE = "MODEL_INFERENCE"
    UNKNOWN = "UNKNOWN"


class ImplementationStatus(StrEnum):
    RUMOR = "RUMOR"
    DRAFT = "DRAFT"
    CONSULTATION = "CONSULTATION"
    ANNOUNCED = "ANNOUNCED"
    APPROVED = "APPROVED"
    IMPLEMENTING = "IMPLEMENTING"
    EXECUTED = "EXECUTED"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    RETRACTED = "RETRACTED"
    UNKNOWN = "UNKNOWN"


class PolicyImpactHorizon(StrEnum):
    INTRADAY = "INTRADAY"
    SHORT_TERM = "SHORT_TERM"
    MEDIUM_TERM = "MEDIUM_TERM"
    LONG_TERM = "LONG_TERM"
    UNKNOWN = "UNKNOWN"


class TextCompleteness(StrEnum):
    FULL_TEXT = "FULL_TEXT"
    PARTIAL_TEXT = "PARTIAL_TEXT"
    TITLE_AND_METADATA = "TITLE_AND_METADATA"
    TITLE_ONLY = "TITLE_ONLY"
    IMAGE_ONLY = "IMAGE_ONLY"
    UNREADABLE = "UNREADABLE"


class PolicyVerificationStatus(StrEnum):
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    VERIFIED_MULTI_SOURCE = "VERIFIED_MULTI_SOURCE"
    SINGLE_OFFICIAL_SOURCE = "SINGLE_OFFICIAL_SOURCE"
    SINGLE_MEDIA_SOURCE = "SINGLE_MEDIA_SOURCE"
    CONFLICT = "CONFLICT"
    UNVERIFIED = "UNVERIFIED"
    RETRACTED = "RETRACTED"


class PolicyRiskFlag(StrEnum):
    DATA_GAP = "DATA_GAP"
    TEXT_INCOMPLETE = "TEXT_INCOMPLETE"
    TITLE_ONLY_SOURCE = "TITLE_ONLY_SOURCE"
    PRIMARY_SOURCE_MISSING = "PRIMARY_SOURCE_MISSING"
    PUBLICATION_TIME_MISSING = "PUBLICATION_TIME_MISSING"
    SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
    SINGLE_MEDIA_SOURCE = "SINGLE_MEDIA_SOURCE"
    EVENT_CONFLICT = "EVENT_CONFLICT"
    UNVERIFIED_EVENT = "UNVERIFIED_EVENT"
    IMPLEMENTATION_UNCERTAIN = "IMPLEMENTATION_UNCERTAIN"
    IMPLEMENTATION_TERMINATED = "IMPLEMENTATION_TERMINATED"
    POLICY_RETRACTED = "POLICY_RETRACTED"
    SYMBOL_RELEVANCE_LOW = "SYMBOL_RELEVANCE_LOW"
    SECTOR_MAPPING_MISSING = "SECTOR_MAPPING_MISSING"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    SHARED_SENTIMENT_EVENT = "SHARED_SENTIMENT_EVENT"
    HISTORICAL_DATA_GAP = "HISTORICAL_DATA_GAP"
    MODE_RESTRICTION = "MODE_RESTRICTION"


class SchemaValidationStatus(StrEnum):
    VALID = "VALID"
    REPAIRED = "REPAIRED"
    INVALID = "INVALID"
    NOT_CALLED = "NOT_CALLED"


_TRADE_INSTRUCTION = re.compile(
    r"(?i)(?:\b(?:BUY|SELL|HOLD|CLOSE_POSITION|SUBMIT_ORDER|PLACE_ORDER)\b|"
    r"建议\s*(?:买入|卖出|持有)|仓位(?:比例)?|止损(?:价)?|止盈(?:价)?|"
    r"目标价|收益保证|自动减仓)"
)


def contains_trade_instruction(value: str) -> bool:
    return bool(_TRADE_INSTRUCTION.search(value))


class SymbolRelevance(PolicyNewsModel):
    symbol: str = Field(min_length=1, max_length=32)
    relevance_weight: float = Field(ge=0, le=1)
    relevance_reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1)
    mapping_version: str = Field(min_length=1)


class SectorRelevance(PolicyNewsModel):
    sector: str = Field(min_length=1, max_length=100)
    relevance_weight: float = Field(ge=0, le=1)
    relevance_reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1)
    mapping_version: str = Field(min_length=1)


class ModelPolicyExtraction(PolicyNewsModel):
    event_category: PolicyEventCategory
    event_type: PolicyEventType
    direction: Literal[-1, 0, 1]
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    fact_type: PolicyFactType
    implementation_status_candidate: ImplementationStatus
    impact_horizon: PolicyImpactHorizon
    affected_symbols: list[str] = Field(default_factory=list, max_length=50)
    affected_sectors: list[str] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=1000)
    key_facts: list[str] = Field(default_factory=list, max_length=20)
    amounts: list[str] = Field(default_factory=list, max_length=20)
    dates: list[str] = Field(default_factory=list, max_length=20)
    entities: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "summary",
        "key_facts",
        "amounts",
        "dates",
        "entities",
        "conditions",
    )
    @classmethod
    def reject_trade_instructions(cls, value: str | list[str]):
        values = [value] if isinstance(value, str) else value
        if any(contains_trade_instruction(item) for item in values):
            raise ValueError("model output must not contain trade instructions")
        return value


class PolicyNewsEventAnalysis(PolicyNewsModel):
    policy_analysis_id: str = Field(min_length=1)
    event_cluster_id: str = Field(min_length=1)
    event_category: PolicyEventCategory
    event_type: PolicyEventType
    direction: Literal[-1, 0, 1]
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    model_confidence: float = Field(ge=0, le=1)
    fact_type: PolicyFactType
    source_level: str = Field(min_length=1)
    source_authority: float = Field(ge=0, le=1)
    implementation_status: ImplementationStatus
    implementation_confidence: float = Field(ge=0, le=1)
    implementation_weight: float = Field(ge=0, le=1)
    impact_horizon: PolicyImpactHorizon
    text_completeness: TextCompleteness
    affected_sectors: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    symbol_relevance: list[SymbolRelevance] = Field(default_factory=list)
    sector_relevance: list[SectorRelevance] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=1000)
    key_facts: list[str] = Field(default_factory=list)
    amounts: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    shared_sentiment_analysis_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    model_call_ids: list[str] = Field(default_factory=list)
    risk_flags: list[PolicyRiskFlag] = Field(default_factory=list)
    verification_status: PolicyVerificationStatus
    verification_weight: float = Field(ge=0, le=1)
    freshness_weight: float = Field(ge=0, le=1)
    relevance_weight: float = Field(ge=0, le=1)
    policy_news_score: float = Field(ge=-1, le=1)
    extractor_version: str = Field(min_length=1)
    scorer_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    mapping_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_time: datetime
    publication_time: datetime | None = None
    data_available_time: datetime
    fetched_at: datetime
    implementation_time: datetime | None = None
    termination_time: datetime | None = None
    generated_at: datetime
    data_cutoff: datetime
    shadow_mode: Literal[True] = True

    @field_validator(
        "event_time",
        "publication_time",
        "data_available_time",
        "fetched_at",
        "implementation_time",
        "termination_time",
        "generated_at",
        "data_cutoff",
    )
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator(
        "summary",
        "key_facts",
        "amounts",
        "dates",
        "entities",
        "conditions",
    )
    @classmethod
    def reject_trade_instructions(cls, value: str | list[str]):
        values = [value] if isinstance(value, str) else value
        if any(contains_trade_instruction(item) for item in values):
            raise ValueError(
                "policy analysis must not contain trade instructions"
            )
        return value

    @field_validator(
        "affected_sectors",
        "affected_symbols",
        "shared_sentiment_analysis_ids",
        "evidence_ids",
        "model_call_ids",
    )
    @classmethod
    def unique_lists(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_analysis(self) -> "PolicyNewsEventAnalysis":
        if self.data_cutoff > self.generated_at:
            raise ValueError("data_cutoff must not be later than generated_at")
        if self.data_available_time > self.data_cutoff:
            raise ValueError(
                "data_available_time must not be later than data_cutoff"
            )
        if {item.symbol for item in self.symbol_relevance} != set(
            self.affected_symbols
        ):
            raise ValueError(
                "symbol_relevance must cover affected_symbols exactly"
            )
        if {item.sector for item in self.sector_relevance} != set(
            self.affected_sectors
        ):
            raise ValueError(
                "sector_relevance must cover affected_sectors exactly"
            )
        if self.direction == 0 and self.policy_news_score != 0:
            raise ValueError("neutral analysis must score zero")
        if self.verification_status in {
            PolicyVerificationStatus.CONFLICT,
            PolicyVerificationStatus.RETRACTED,
        } and self.policy_news_score != 0:
            raise ValueError("conflicted or retracted analysis must score zero")
        if (
            self.implementation_status
            in {ImplementationStatus.TERMINATED, ImplementationStatus.RETRACTED}
            and self.direction > 0
            and self.policy_news_score != 0
        ):
            raise ValueError(
                "terminated or retracted positive events must score zero"
            )
        return self


class PolicyNewsSnapshotBase(PolicyNewsModel):
    snapshot_id: str = Field(min_length=1)
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    generated_at: datetime
    event_count: int = Field(ge=0)
    positive_event_count: int = Field(ge=0)
    negative_event_count: int = Field(ge=0)
    neutral_event_count: int = Field(ge=0)
    weighted_policy_score: float = Field(ge=-1, le=1)
    high_authority_event_count: int = Field(ge=0)
    implemented_event_count: int = Field(ge=0)
    conflict_event_count: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    horizon_scores: dict[str, float] = Field(default_factory=dict)
    top_positive_events: list[str] = Field(default_factory=list)
    top_negative_events: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    model_call_ids: list[str] = Field(default_factory=list)
    shared_sentiment_event_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[PolicyRiskFlag] = Field(default_factory=list)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str = Field(min_length=1)
    shadow_mode: Literal[True] = True

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class PolicyNewsSymbolSnapshot(PolicyNewsSnapshotBase):
    symbol: str = Field(min_length=1, max_length=32)


class PolicyNewsSectorSnapshot(PolicyNewsSnapshotBase):
    sector: str = Field(min_length=1, max_length=100)


class PolicyNewsAnalyzeRequest(PolicyNewsModel):
    analysis_mode: AnalysisMode = AnalysisMode.RESEARCH
    data_cutoff: datetime
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    sector: str | None = Field(default=None, min_length=1, max_length=100)
    event_cluster_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_model_calls: bool = False
    allow_external_fetch: bool = False

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value

    @model_validator(mode="after")
    def enforce_mode_boundary(self) -> "PolicyNewsAnalyzeRequest":
        if self.analysis_mode == AnalysisMode.SCREENING and (
            self.allow_model_calls or self.allow_external_fetch
        ):
            raise ValueError(
                "SCREENING does not allow model calls or external fetch"
            )
        if self.symbol is not None and self.sector is not None:
            raise ValueError("choose either symbol or sector, not both")
        return self


class PolicyNewsAnalyzeResponse(PolicyNewsModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    shadow_mode: Literal[True] = True
    analyses: list[PolicyNewsEventAnalysis] = Field(default_factory=list)
    symbol_snapshot: PolicyNewsSymbolSnapshot | None = None
    sector_snapshot: PolicyNewsSectorSnapshot | None = None
    factor_output: FactorOutput | None = None
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[PolicyRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    implementation_status: list[ImplementationStatus] = Field(
        default_factory=list
    )
    text_completeness: list[TextCompleteness] = Field(default_factory=list)
    shared_sentiment_event_ids: list[str] = Field(default_factory=list)


class PolicyNewsReadResponse(PolicyNewsModel):
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    shadow_mode: Literal[True] = True
    result: (
        PolicyNewsEventAnalysis
        | PolicyNewsSymbolSnapshot
        | PolicyNewsSectorSnapshot
    )
    missing_fields: list[str] = Field(default_factory=list)
    risk_flags: list[PolicyRiskFlag] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    implementation_status: list[ImplementationStatus] = Field(
        default_factory=list
    )
    text_completeness: list[TextCompleteness] = Field(default_factory=list)
    shared_sentiment_event_ids: list[str] = Field(default_factory=list)


class PolicyNewsEvaluationRequest(PolicyNewsModel):
    snapshot_id: str = Field(min_length=1)
    data_cutoff: datetime
    actually_implemented: bool | None = None
    terminated_or_retracted: bool | None = None

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include a timezone")
        return value


class PolicyNewsEvaluation(PolicyNewsModel):
    evaluation_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    symbol: str | None = None
    sector: str | None = None
    event_time: datetime | None = None
    publication_time: datetime | None = None
    implementation_status_at_analysis: ImplementationStatus
    data_cutoff: datetime
    policy_news_score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    max_rise: float | None = None
    max_drawdown: float | None = None
    actually_implemented: bool | None = None
    terminated_or_retracted: bool | None = None
    data_complete: bool
    evidence_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    generated_at: datetime
    algorithm_version: str = Field(min_length=1)


__all__ = [
    "ImplementationStatus",
    "ModelPolicyExtraction",
    "PolicyEventCategory",
    "PolicyEventType",
    "PolicyFactType",
    "PolicyImpactHorizon",
    "PolicyNewsAnalyzeRequest",
    "PolicyNewsAnalyzeResponse",
    "PolicyNewsEvaluation",
    "PolicyNewsEvaluationRequest",
    "PolicyNewsEventAnalysis",
    "PolicyNewsReadResponse",
    "PolicyNewsSectorSnapshot",
    "PolicyNewsSymbolSnapshot",
    "PolicyRiskFlag",
    "PolicyVerificationStatus",
    "SchemaValidationStatus",
    "SectorRelevance",
    "SymbolRelevance",
    "TextCompleteness",
    "contains_trade_instruction",
]
