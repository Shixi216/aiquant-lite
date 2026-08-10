from __future__ import annotations

from trading.schemas import AdversarialReview, StrategyProposal


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
