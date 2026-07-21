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
