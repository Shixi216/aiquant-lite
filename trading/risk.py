from __future__ import annotations

from datetime import datetime

from trading.schemas import (
    AdversarialReview,
    PortfolioState,
    RiskLimits,
    RiskVerdict,
    StrategyProposal,
)


def evaluate_strategy_risk(
    proposal: StrategyProposal,
    portfolio: PortfolioState,
    limits: RiskLimits,
) -> RiskVerdict:
    reasons: list[str] = []
    veto = False
    if portfolio.kill_switch:
        veto = True
        reasons.append("Emergency kill switch is active")
    if portfolio.daily_pnl_pct <= -limits.max_daily_loss:
        veto = True
        reasons.append("Daily loss limit has been reached")
    if portfolio.gross_exposure >= limits.max_gross_exposure and proposal.target_weight > 0:
        veto = True
        reasons.append("Gross exposure limit has been reached")
    adjusted = min(proposal.target_weight, limits.max_position_weight)
    if adjusted < proposal.target_weight:
        reasons.append("Target weight was capped by max_position_weight")
    if 1 - portfolio.gross_exposure < limits.min_cash_weight and adjusted > portfolio.current_weight:
        veto = True
        reasons.append("Minimum cash reserve would be breached")
    return RiskVerdict(
        approved=not veto,
        vetoed=veto,
        reasons=reasons,
        adjusted_target_weight=0 if veto else adjusted,
        human_approval_required=limits.require_human_approval,
    )


def adversarial_review(
    proposal: StrategyProposal,
    evidence_count: int,
    technical_confidence: float,
    fundamental_confidence: float,
) -> AdversarialReview:
    findings: list[str] = []
    actions: list[str] = []
    if evidence_count < 3:
        findings.append("Too few auditable evidence references")
        actions.append("Collect at least three current evidence records")
    if proposal.action.value == "buy" and fundamental_confidence < 0.3:
        findings.append("Buy case lacks reliable fundamental confirmation")
        actions.append("Require human approval or add current financial evidence")
    if abs(technical_confidence - fundamental_confidence) > 0.6:
        findings.append("Agent confidence is materially inconsistent")
        actions.append("Reconcile conflicting assumptions before execution")
    return AdversarialReview(passed=not findings, findings=findings, required_actions=actions)


def validate_order_clock(price_time: datetime, max_age_seconds: int) -> str | None:
    now = datetime.now().astimezone()
    normalized = price_time if price_time.tzinfo else price_time.astimezone()
    age = (now - normalized).total_seconds()
    if age < -5:
        return "Reference price timestamp is in the future"
    if age > max_age_seconds:
        return f"Reference price is stale ({age:.0f}s old)"
    return None
