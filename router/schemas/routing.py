from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    """Business risk attached to one routed model request."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BudgetTier(StrEnum):
    """Deterministic model-call budget selected by the caller."""

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class RiskReviewStatus(StrEnum):
    """Outcome of the optional automated risk-controller escalation."""

    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RiskReviewDecision(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class RiskReviewOutput(BaseModel):
    """Validated JSON contract produced by the risk controller."""

    model_config = ConfigDict(extra="forbid")

    decision: RiskReviewDecision
    assessed_risk_level: RiskLevel
    findings: list[str] = Field(default_factory=list, max_length=20)
    required_actions: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


class RiskReviewResult(BaseModel):
    """Safe escalation result attached to the original Router response."""

    model_config = ConfigDict(extra="forbid")

    status: RiskReviewStatus
    provider: str | None = None
    model: str | None = None
    call_ids: list[str] = Field(default_factory=list)
    output: RiskReviewOutput | None = None
    reason: str | None = None


class RoutingDecision(BaseModel):
    """Auditable controls applied before a provider is called."""

    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel
    budget_tier: BudgetTier
    provider: str
    model: str
    max_output_tokens_per_call: int = Field(ge=16, le=8192)
    max_physical_calls: int = Field(ge=1, le=10)
    temperature_cap: float = Field(ge=0, le=2)
    human_review_required: bool
    escalation_role: str | None = None
    reasons: list[str] = Field(default_factory=list)
