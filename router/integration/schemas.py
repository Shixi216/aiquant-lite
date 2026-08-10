from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.version import PROJECT_VERSION


API_VERSION = PROJECT_VERSION
ResponseData = TypeVar("ResponseData")


class IntegrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    FAILED = "FAILED"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    FAILED = "FAILED"


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_DATA = "MISSING_DATA"
    NOT_FOUND = "NOT_FOUND"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    STALE_DATA = "STALE_DATA"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class UnifiedResponse(IntegrationModel, Generic[ResponseData]):
    request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    status: ResponseStatus
    data: ResponseData | None = None
    error_code: ErrorCode | None = None
    sanitized_error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    data_cutoff: datetime | None = None
    generated_at: datetime
    api_version: str = API_VERSION
    research_only: Literal[True] = True

    @field_validator("data_cutoff", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value


class ToolMetadata(IntegrationModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_seconds: int = Field(ge=1, le=300)
    read_only: bool
    requires_confirmation: bool
    data_cutoff_rule: str
    risk_statement: str


class ToolCatalog(IntegrationModel):
    tools: list[ToolMetadata]
    tool_count: int = Field(ge=1)
    arbitrary_sql_supported: Literal[False] = False
    raw_database_access_supported: Literal[False] = False
    order_tools_supported: Literal[False] = False


class WorkflowStep(StrEnum):
    PARSE = "PARSE"
    SCAN = "SCAN"
    RESEARCH = "RESEARCH"
    DECISION = "DECISION"


class WorkflowContext(IntegrationModel):
    step: WorkflowStep
    parent_request_id: str | None = Field(
        default=None,
        pattern=r"^req_[0-9a-f]{24}$",
    )
    network_request_count: int = Field(default=0, ge=0)
    model_call_count: int = Field(default=0, ge=0)
    decision_called: bool = False
    order_created: Literal[False] = False
    is_trade_recommendation: Literal[False] = False


class ParseWorkflowRequest(IntegrationModel):
    query: str = Field(min_length=1, max_length=2000)
    top_n: int = Field(default=20, ge=10, le=30)
    data_cutoff: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class ParseWorkflowResult(IntegrationModel):
    workflow: WorkflowContext
    parsed_query: dict[str, Any] | None
    condition_summary: list[str]
    clarification_required: bool
    clarification_questions: list[str]
    unsupported_fragments: list[str]


class ScanWorkflowRequest(ParseWorkflowRequest):
    parent_request_id: str | None = Field(
        default=None,
        pattern=r"^req_[0-9a-f]{24}$",
    )


class ScanWorkflowResult(IntegrationModel):
    workflow: WorkflowContext
    scan: dict[str, Any]


class ResearchWorkflowRequest(IntegrationModel):
    symbols: list[str] = Field(min_length=1, max_length=30)
    data_cutoff: datetime
    parent_request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    capture_snapshot: bool = False
    confirm_snapshot_capture: bool = False
    ai_deep_analysis_limit: int = Field(default=10, ge=0, le=10)

    @model_validator(mode="after")
    def require_capture_confirmation(self) -> "ResearchWorkflowRequest":
        if self.capture_snapshot and not self.confirm_snapshot_capture:
            raise ValueError("snapshot capture requires explicit confirmation")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("research symbols must be unique")
        return self


class ResearchWorkflowResult(IntegrationModel):
    workflow: WorkflowContext
    research: dict[str, Any]
    ai_deep_analysis_symbols: list[str] = Field(max_length=10)


class DecisionWorkflowRequest(IntegrationModel):
    symbol: str = Field(pattern=r"^\d{6}\.(?:SH|SZ|BJ)$")
    data_cutoff: datetime
    parent_request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    technical_score: float = Field(ge=-1, le=1)
    technical_confidence: float = Field(ge=0, le=1)
    fundamental_score: float = Field(ge=-1, le=1)
    fundamental_confidence: float = Field(ge=0, le=1)
    hard_veto: bool = False
    confirmation: str

    @model_validator(mode="after")
    def require_explicit_confirmation(self) -> "DecisionWorkflowRequest":
        expected = f"CONFIRM_DECISION:{self.symbol}"
        if self.confirmation != expected:
            raise ValueError(
                "decision requires exact confirmation CONFIRM_DECISION:<symbol>"
            )
        return self


class DecisionWorkflowResult(IntegrationModel):
    workflow: WorkflowContext
    formal_result: dict[str, Any]
    shadow_composite: dict[str, Any]
    factor_coverage: dict[str, Any]
    available_factors: list[str]
    missing_factors: list[str]
    evidence_ids: list[str]
    decision_packet_created: Literal[False] = False
    formal_weights: dict[str, float]
    shadow_formal_strategy_weight: Literal[0.0] = 0.0


class WeComMessageType(StrEnum):
    TEXT_QUERY = "TEXT_QUERY"
    PARSE_CONFIRMATION = "PARSE_CONFIRMATION"
    CANDIDATE_LIST = "CANDIDATE_LIST"
    CANDIDATE_DETAIL = "CANDIDATE_DETAIL"
    RESEARCH_SUMMARY = "RESEARCH_SUMMARY"
    DECISION_SUMMARY = "DECISION_SUMMARY"
    MISSING_DATA = "MISSING_DATA"
    VETO = "VETO"
    STATUS = "STATUS"
    EXPERIMENT_SUMMARY = "EXPERIMENT_SUMMARY"


class WeComSimulationRequest(IntegrationModel):
    message_id: str = Field(min_length=1, max_length=128)
    message_type: WeComMessageType
    text: str = Field(min_length=1, max_length=8000)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    decision_confirmation: str | None = None


class WeComSimulationResult(IntegrationModel):
    delivery_mode: Literal["LOCAL_SIMULATION"] = "LOCAL_SIMULATION"
    configuration_status: Literal["CONFIGURED", "NOT_CONFIGURED"]
    message_id: str
    duplicate: bool
    sent: Literal[False] = False
    message_type: WeComMessageType
    rendered_text: str
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    requires_second_confirmation: bool
    trade_action_available: Literal[False] = False


class ComponentHealth(IntegrationModel):
    name: str
    status: HealthStatus
    detail: str
    required: bool


class SystemHealth(IntegrationModel):
    overall_status: HealthStatus
    components: list[ComponentHealth]
    router_port: Literal[8765] = 8765
    data_hub_port: Literal[8766] = 8766
    database_read_only_ok: bool
    migration_checksums_ok: bool
    env_file_exists: bool
    provider_configured: dict[str, bool]
    research_coverage: dict[str, int]
    disk_free_bytes: int = Field(ge=0)
    latest_snapshot_time: datetime | None
    snapshot_stale: bool
    formal_strategy_status: Literal["INSUFFICIENT_COVERAGE"]
    live_trading_status: Literal["NOT_SUPPORTED"] = "NOT_SUPPORTED"


class DoctorReport(IntegrationModel):
    health: SystemHealth
    recommendations: list[str]
    mutations_performed: Literal[False] = False


class OpenAPISummary(IntegrationModel):
    service: str
    path_count: int = Field(ge=0)
    operation_count: int = Field(ge=0)
    schema_count: int = Field(ge=0)
    duplicate_operation_ids: list[str]
    duplicate_schema_names: list[str]
    undocumented_operations: list[str]


__all__ = [
    "API_VERSION",
    "ComponentHealth",
    "DecisionWorkflowRequest",
    "DecisionWorkflowResult",
    "DoctorReport",
    "ErrorCode",
    "HealthStatus",
    "OpenAPISummary",
    "ParseWorkflowRequest",
    "ParseWorkflowResult",
    "ResearchWorkflowRequest",
    "ResearchWorkflowResult",
    "ResponseStatus",
    "ScanWorkflowRequest",
    "ScanWorkflowResult",
    "SystemHealth",
    "ToolCatalog",
    "ToolMetadata",
    "UnifiedResponse",
    "WeComMessageType",
    "WeComSimulationRequest",
    "WeComSimulationResult",
    "WorkflowContext",
    "WorkflowStep",
]
