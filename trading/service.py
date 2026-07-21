from __future__ import annotations

import asyncio

from trading.analytics import fundamental_signal, technical_signal
from trading.risk import adversarial_review, evaluate_strategy_risk
from trading.schemas import (
    Action,
    AgentOpinion,
    DecisionRequest,
    DecisionTrace,
    Signal,
    StrategyProposal,
)


def _stance(score: float) -> str:
    if score > 0.2:
        return "bullish"
    if score < -0.2:
        return "bearish"
    return "neutral"


def _opinion(signal: Signal) -> AgentOpinion:
    return AgentOpinion(
        role=signal.role,
        stance=_stance(signal.score),
        summary=signal.summary,
        evidence=signal.evidence,
        counterpoints=signal.risks,
        confidence=signal.confidence,
    )


def _generate_strategy(technical: Signal, fundamental: Signal) -> StrategyProposal:
    combined = technical.score * 0.6 + fundamental.score * 0.4
    confidence = technical.confidence * 0.6 + fundamental.confidence * 0.4
    if combined >= 0.2:
        action = Action.BUY
        target = min(0.20, max(0.02, combined * 0.20))
    elif combined <= -0.2:
        action = Action.SELL
        target = 0
    else:
        action = Action.HOLD
        target = 0
    return StrategyProposal(
        name="auditable_multi_factor_v1",
        action=action,
        target_weight=target,
        confidence=max(0, min(1, confidence)),
        entry_rule="Technical/fundamental weighted score >= 0.20 and all safety gates pass.",
        exit_rule="Score <= -0.20, stop loss, take profit, or risk veto.",
        invalidation_rules=[
            "stale or contradictory market data",
            "daily loss or exposure limit reached",
            "risk controller veto",
            "adversarial evidence-gap review",
        ],
        evidence=[*technical.evidence, *fundamental.evidence],
    )


class TradingDecisionService:
    """Runs independent agents concurrently and produces an auditable decision."""

    async def decide(self, request: DecisionRequest) -> DecisionTrace:
        technical, fundamental = await asyncio.gather(
            asyncio.to_thread(technical_signal, request.bars),
            asyncio.to_thread(fundamental_signal, request.fundamentals),
        )
        proposal = _generate_strategy(technical, fundamental)
        risk = evaluate_strategy_risk(proposal, request.portfolio, request.risk_limits)
        evidence = list(dict.fromkeys([*request.evidence_refs, *proposal.evidence]))
        review = adversarial_review(
            proposal,
            len(evidence),
            technical.confidence,
            fundamental.confidence,
        )
        opinions = [_opinion(technical), _opinion(fundamental)]
        opinions.append(
            AgentOpinion(
                role="strategy_agent",
                stance=_stance(technical.score * 0.6 + fundamental.score * 0.4),
                summary=f"Generated {proposal.name}: {proposal.action.value}.",
                evidence=proposal.evidence,
                counterpoints=proposal.invalidation_rules,
                confidence=proposal.confidence,
            )
        )
        if risk.vetoed:
            final_action = Action.VETO
            final_weight = 0
        elif not review.passed and proposal.action == Action.BUY:
            final_action = Action.HOLD
            final_weight = request.portfolio.current_weight
        else:
            final_action = proposal.action
            final_weight = risk.adjusted_target_weight
        summary = [
            technical.summary,
            fundamental.summary,
            f"Strategy proposal: {proposal.action.value} at target weight {proposal.target_weight:.2%}.",
        ]
        summary.extend(f"Risk veto: {reason}" for reason in risk.reasons)
        summary.extend(f"Adversarial finding: {finding}" for finding in review.findings)
        return DecisionTrace(
            symbol=request.symbol,
            as_of=max(bar.trade_date for bar in request.bars),
            opinions=opinions,
            proposal=proposal,
            risk_verdict=risk,
            adversarial_review=review,
            final_action=final_action,
            final_target_weight=final_weight,
            rationale_summary=summary,
            evidence_refs=evidence,
        )
