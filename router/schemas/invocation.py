from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from router.schemas.routing import (
    BudgetTier,
    RiskLevel,
    RiskReviewResult,
    RoutingDecision,
)


class RouterInvokeRequest(BaseModel):
    """Request sent to one specialist role."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=100)
    symbol: str | None = Field(
        default=None,
        max_length=32,
    )
    prompt: str = Field(min_length=1, max_length=50000)
    system_prompt: str | None = Field(
        default=None,
        max_length=10000,
    )
    temperature: float = Field(
        default=0.2,
        ge=0,
        le=2,
    )
    max_tokens: int = Field(
        default=512,
        ge=16,
        le=8192,
    )
    risk_level: RiskLevel = RiskLevel.MEDIUM
    budget_tier: BudgetTier = BudgetTier.STANDARD


class RouterInvokeResponse(BaseModel):
    """Normalized result returned by a specialist model."""

    model_config = ConfigDict(extra="forbid")

    role: str
    provider: str
    model: str
    content: str
    latency_ms: int
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    validated: bool = False
    attempts: int = Field(default=1, ge=1, le=3)
    task_id: str | None = None
    call_ids: list[str] = Field(default_factory=list)
    routing: RoutingDecision | None = None
    risk_review: RiskReviewResult | None = None
