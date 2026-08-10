from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from data_hub.schemas.unified import FactorType
from trading.scanner.models import (
    AnomalyType,
    FreshnessPolicy,
    MissingDataPolicy,
    ParserType,
    ResearchStatus,
    ScannerRiskFlag,
)
from trading.scanner.query_ast import FilterNode, SortNode
from trading.schemas import AnalysisMode


class ScannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ScannerQueryPlan(ScannerModel):
    query_id: str = Field(pattern=r"^sq_[0-9a-f]{24}$")
    original_query: str = Field(min_length=1, max_length=2000)
    normalized_query: str = Field(min_length=1, max_length=2000)
    analysis_mode: AnalysisMode = AnalysisMode.SCREENING
    data_cutoff: datetime
    universe: Literal["ALL_A_SHARES"] = "ALL_A_SHARES"
    include_boards: list[str] = Field(default_factory=list)
    exclude_boards: list[str] = Field(default_factory=list)
    include_industries: list[str] = Field(default_factory=list)
    exclude_industries: list[str] = Field(default_factory=list)
    include_symbols: list[str] = Field(default_factory=list)
    exclude_symbols: list[str] = Field(default_factory=list)
    filters: list[FilterNode] = Field(default_factory=list)
    anomaly_conditions: list[AnomalyType] = Field(default_factory=list)
    sort_fields: list[SortNode] = Field(default_factory=list, max_length=5)
    top_n: int = Field(default=20, ge=10, le=30)
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.EXCLUDE
    freshness_policy: FreshnessPolicy = FreshnessPolicy.FLAG_STALE
    ambiguity_flags: list[ScannerRiskFlag] = Field(default_factory=list)
    parser_type: ParserType = ParserType.LOCAL
    parser_version: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scanner timestamps must include timezone")
        return value


class ScannerParseRequest(ScannerModel):
    query: str = Field(min_length=1, max_length=2000)
    analysis_mode: AnalysisMode = AnalysisMode.SCREENING
    data_cutoff: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    top_n: int | None = Field(default=None, ge=10, le=30)
    allow_parser_model: bool = False
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.EXCLUDE
    freshness_policy: FreshnessPolicy = FreshnessPolicy.FLAG_STALE


class ScannerParseResponse(ScannerModel):
    parsed_query: ScannerQueryPlan | None
    condition_summary: list[str]
    clarification_required: bool
    clarification_questions: list[str] = Field(default_factory=list)
    unsupported_fragments: list[str] = Field(default_factory=list)
    parser_model_call_count: int = Field(default=0, ge=0, le=1)
    risk_flags: list[ScannerRiskFlag] = Field(default_factory=list)
    is_trade_recommendation: Literal[False] = False


class ScannerScanRequest(ScannerModel):
    query: str | None = Field(default=None, max_length=2000)
    plan: ScannerQueryPlan | None = None
    analysis_mode: AnalysisMode = AnalysisMode.SCREENING
    data_cutoff: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    top_n: int | None = Field(default=None, ge=10, le=30)
    allow_parser_model: bool = False
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.EXCLUDE
    freshness_policy: FreshnessPolicy = FreshnessPolicy.FLAG_STALE
    persist_run: bool = False
    explicit_decision_confirmation: bool = False

    @model_validator(mode="after")
    def require_exactly_one_query_source(self) -> "ScannerScanRequest":
        if (self.query is None) == (self.plan is None):
            raise ValueError("provide exactly one of query or plan")
        return self


class ScannerScoreComponents(ScannerModel):
    query_match: float = Field(ge=0, le=1)
    anomaly_strength: float = Field(ge=0, le=1)
    technical: float | None = Field(default=None, ge=0, le=1)
    capital_flow: float | None = Field(default=None, ge=0, le=1)
    shadow_composite: float | None = Field(default=None, ge=0, le=1)
    factor_coverage: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    liquidity: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    risk_penalty: float = Field(ge=0, le=1)


class ScannerCandidateCard(ScannerModel):
    rank: int = Field(ge=1, le=30)
    symbol: str
    short_name: str | None = None
    board: str
    industry: str | None = None
    snapshot_time: datetime | None = None
    current_price: float | None = None
    change_pct: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    anomaly_types: list[AnomalyType] = Field(default_factory=list)
    scanner_score: float = Field(ge=0, le=1)
    score_components: ScannerScoreComponents
    technical_score: float | None = Field(default=None, ge=-1, le=1)
    capital_flow_score: float | None = Field(default=None, ge=-1, le=1)
    shadow_composite_score: float | None = Field(default=None, ge=-1, le=1)
    composite_confidence: float | None = Field(default=None, ge=0, le=1)
    factor_coverage: str = Field(pattern=r"^[0-5]/5$")
    available_factors: list[FactorType]
    missing_factors: list[FactorType]
    missing_fields: list[str] = Field(default_factory=list)
    top_positive_factors: list[FactorType] = Field(default_factory=list)
    top_negative_factors: list[FactorType] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    risk_flags: list[ScannerRiskFlag] = Field(default_factory=list)
    data_freshness: Literal["FRESH", "STALE", "MISSING"]
    evidence_summary: dict[str, Any]
    research_status: ResearchStatus
    candidate_layer: Literal["CORE", "NEAR", "CONTROL"] = "CORE"
    layer_reason: str = "完全命中现有扫描条件"
    is_trade_recommendation: Literal[False] = False


class ScannerCandidateLayers(ScannerModel):
    core: list[ScannerCandidateCard] = Field(default_factory=list)
    near: list[ScannerCandidateCard] = Field(default_factory=list)
    control: list[ScannerCandidateCard] = Field(default_factory=list)


class ScannerPerformance(ScannerModel):
    cold_cache: bool
    elapsed_ms: float = Field(ge=0)
    data_read_ms: float = Field(ge=0)
    feature_compute_ms: float = Field(ge=0)
    filter_ms: float = Field(ge=0)
    anomaly_ms: float = Field(ge=0)
    ranking_ms: float = Field(ge=0)
    result_card_ms: float = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    database_session_count: int = Field(ge=0)
    database_query_count: int = Field(ge=0)
    network_request_count: Literal[0] = 0
    model_call_count: Literal[0] = 0
    decision_packet_count: Literal[0] = 0


class ScannerScanResponse(ScannerModel):
    run_id: str = Field(pattern=r"^sr_[0-9a-f]{24}$")
    parsed_query: ScannerQueryPlan
    condition_summary: list[str]
    clarification_required: Literal[False] = False
    analysis_mode: AnalysisMode
    data_cutoff: datetime
    snapshot_time: datetime | None
    stale: bool
    universe_count: int = Field(ge=0)
    scanned_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=30)
    no_match_reason: str | None = None
    candidates: list[ScannerCandidateCard]
    candidate_layers: ScannerCandidateLayers = Field(
        default_factory=ScannerCandidateLayers
    )
    performance: ScannerPerformance
    factor_coverage_summary: dict[str, int]
    missing_data_summary: dict[str, int]
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    persisted: bool
    network_request_count: Literal[0] = 0
    model_call_count: Literal[0] = 0
    decision_called: Literal[False] = False
    formal_action_changed: Literal[False] = False
    hard_veto_changed: Literal[False] = False
    is_trade_recommendation: Literal[False] = False


class ScannerEvaluationRequest(ScannerModel):
    run_id: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    persist: bool = False


class ScannerEvaluationItem(ScannerModel):
    run_id: str
    symbol: str
    rank: int
    scanner_score: float
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    maximum_upside: float | None = None
    maximum_drawdown: float | None = None
    is_suspended: bool | None = None
    price_limit_up: bool | None = None
    price_limit_down: bool | None = None
    data_complete: bool
    status: Literal["COMPLETE", "INSUFFICIENT_DATA"]


class ScannerEvaluationResponse(ScannerModel):
    run_id: str
    items: list[ScannerEvaluationItem]
    persisted_count: int
    ranking_mutated: Literal[False] = False
    used_for_candidate_generation: Literal[False] = False
    stable_prediction_claim: Literal[False] = False
    is_trade_recommendation: Literal[False] = False


__all__ = [
    "ScannerCandidateCard",
    "ScannerCandidateLayers",
    "ScannerEvaluationItem",
    "ScannerEvaluationRequest",
    "ScannerEvaluationResponse",
    "ScannerParseRequest",
    "ScannerParseResponse",
    "ScannerPerformance",
    "ScannerQueryPlan",
    "ScannerScanRequest",
    "ScannerScanResponse",
    "ScannerScoreComponents",
]
