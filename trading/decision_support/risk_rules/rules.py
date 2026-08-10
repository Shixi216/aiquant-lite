from __future__ import annotations

from trading.schemas import (
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
