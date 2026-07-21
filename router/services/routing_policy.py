from __future__ import annotations

from dataclasses import dataclass

from router.registry import RoleSpec
from router.schemas.routing import BudgetTier, RiskLevel, RoutingDecision


@dataclass(frozen=True)
class BudgetLimits:
    max_output_tokens_per_call: int
    max_physical_calls: int


BUDGET_LIMITS: dict[BudgetTier, BudgetLimits] = {
    BudgetTier.ECONOMY: BudgetLimits(
        max_output_tokens_per_call=512,
        max_physical_calls=1,
    ),
    BudgetTier.STANDARD: BudgetLimits(
        max_output_tokens_per_call=2048,
        max_physical_calls=4,
    ),
    BudgetTier.PREMIUM: BudgetLimits(
        max_output_tokens_per_call=8192,
        max_physical_calls=8,
    ),
}

RISK_TEMPERATURE_CAPS: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.8,
    RiskLevel.MEDIUM: 0.4,
    RiskLevel.HIGH: 0.2,
    RiskLevel.CRITICAL: 0.0,
}

MINIMUM_BUDGET: dict[RiskLevel, BudgetTier] = {
    RiskLevel.LOW: BudgetTier.ECONOMY,
    RiskLevel.MEDIUM: BudgetTier.ECONOMY,
    RiskLevel.HIGH: BudgetTier.STANDARD,
    RiskLevel.CRITICAL: BudgetTier.PREMIUM,
}

_BUDGET_RANK = {
    BudgetTier.ECONOMY: 0,
    BudgetTier.STANDARD: 1,
    BudgetTier.PREMIUM: 2,
}


class RoutingPolicyViolation(ValueError):
    """Raised before model execution when risk and budget are incompatible."""


class ModelCallBudgetExceeded(RuntimeError):
    """Raised before a provider call would exceed the selected call budget."""


class RoutingPolicyService:
    """Resolve deterministic risk controls without calling an LLM."""

    def decide(
        self,
        *,
        role: RoleSpec,
        risk_level: RiskLevel,
        budget_tier: BudgetTier,
    ) -> RoutingDecision:
        minimum = MINIMUM_BUDGET[risk_level]
        if _BUDGET_RANK[budget_tier] < _BUDGET_RANK[minimum]:
            raise RoutingPolicyViolation(
                f"risk_level={risk_level.value} requires budget_tier>={minimum.value}"
            )

        limits = BUDGET_LIMITS[budget_tier]
        review_required = risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        escalation_role = "risk_controller" if review_required else None
        reasons = [
            f"output tokens capped by {budget_tier.value} budget",
            f"temperature capped for {risk_level.value} risk",
        ]
        if review_required:
            reasons.append("high-risk output requires human review")
            reasons.append("risk_controller escalation is requested when available")

        return RoutingDecision(
            risk_level=risk_level,
            budget_tier=budget_tier,
            provider=role.provider,
            model=role.preferred_model,
            max_output_tokens_per_call=limits.max_output_tokens_per_call,
            max_physical_calls=limits.max_physical_calls,
            temperature_cap=RISK_TEMPERATURE_CAPS[risk_level],
            human_review_required=review_required,
            escalation_role=escalation_role,
            reasons=reasons,
        )


def routing_policy_catalog() -> dict[str, object]:
    """Return a public, credential-free description of the policy matrix."""
    return {
        "budgets": {
            tier.value: {
                "max_output_tokens_per_call": limits.max_output_tokens_per_call,
                "max_physical_calls": limits.max_physical_calls,
            }
            for tier, limits in BUDGET_LIMITS.items()
        },
        "risks": {
            risk.value: {
                "temperature_cap": RISK_TEMPERATURE_CAPS[risk],
                "minimum_budget_tier": MINIMUM_BUDGET[risk].value,
                "human_review_required": risk in {RiskLevel.HIGH, RiskLevel.CRITICAL},
            }
            for risk in RiskLevel
        },
    }
