from __future__ import annotations

from statistics import fmean

from trading.schemas import FundamentalSnapshot, Signal


def fundamental_signal(snapshot: FundamentalSnapshot | None) -> Signal:
    if snapshot is None:
        return Signal(
            role="fundamental_agent",
            score=0,
            confidence=0.1,
            summary="No fundamental snapshot was supplied; score is neutral.",
            risks=["Fundamental evidence is missing"],
        )
    components: list[float] = []
    evidence: list[str] = list(snapshot.evidence_refs)
    risks: list[str] = []
    if snapshot.roe is not None:
        components.append(max(-1, min(1, (snapshot.roe - 0.08) / 0.12)))
        evidence.append(f"ROE={snapshot.roe:.4f}")
    if snapshot.revenue_growth is not None:
        components.append(max(-1, min(1, snapshot.revenue_growth / 0.25)))
        evidence.append(f"revenue_growth={snapshot.revenue_growth:.4f}")
    if snapshot.debt_ratio is not None:
        components.append(max(-1, min(1, (0.65 - snapshot.debt_ratio) / 0.35)))
        evidence.append(f"debt_ratio={snapshot.debt_ratio:.4f}")
        if snapshot.debt_ratio > 0.75:
            risks.append("Debt ratio exceeds 75%")
    if snapshot.operating_cash_flow_positive is not None:
        components.append(0.5 if snapshot.operating_cash_flow_positive else -0.8)
        if not snapshot.operating_cash_flow_positive:
            risks.append("Operating cash flow is negative")
    if snapshot.pe_ttm is not None and snapshot.pe_ttm <= 0:
        risks.append("PE is non-positive; earnings may be negative")
    score = fmean(components) if components else 0.0
    confidence = min(0.9, 0.2 + len(components) * 0.15)
    return Signal(
        role="fundamental_agent",
        score=score,
        confidence=confidence,
        summary=f"Fundamental composite uses {len(components)} available factors.",
        evidence=evidence,
        risks=risks,
    )
