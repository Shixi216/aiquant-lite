from __future__ import annotations

import math
from statistics import fmean

from trading.schemas import Bar, FundamentalSnapshot, Signal


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    changes = [b - a for a, b in zip(closes[-period - 1 : -1], closes[-period:])]
    gains = fmean(max(change, 0) for change in changes)
    losses = fmean(max(-change, 0) for change in changes)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def technical_signal(bars: list[Bar]) -> Signal:
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    closes = [bar.close for bar in ordered]
    sma20 = fmean(closes[-20:])
    sma60 = fmean(closes[-60:]) if len(closes) >= 60 else fmean(closes)
    ema12 = _ema(closes[-60:], 12)
    ema26 = _ema(closes[-60:], 26)
    rsi14 = _rsi(closes)
    trend = max(-1.0, min(1.0, (sma20 / sma60 - 1) * 12))
    momentum = max(-1.0, min(1.0, (ema12 / ema26 - 1) * 25))
    rsi_score = max(-1.0, min(1.0, (50 - abs(rsi14 - 50)) / 50))
    if rsi14 > 75:
        rsi_score = -0.5
    elif rsi14 < 25:
        rsi_score = 0.25
    score = max(-1.0, min(1.0, trend * 0.5 + momentum * 0.35 + rsi_score * 0.15))
    risks: list[str] = []
    if rsi14 > 75:
        risks.append("RSI is overbought; chasing risk is elevated")
    if ordered[-1].volume == 0:
        risks.append("Latest bar has zero volume")
    return Signal(
        role="technical_agent",
        score=score,
        confidence=min(0.9, 0.55 + len(ordered) / 500),
        summary=(
            f"close={closes[-1]:.2f}, SMA20={sma20:.2f}, SMA60={sma60:.2f}, "
            f"MACD proxy={ema12 - ema26:.3f}, RSI14={rsi14:.1f}"
        ),
        evidence=[f"bar:{bar.trade_date.isoformat()}:{bar.close:.4f}" for bar in ordered[-5:]],
        risks=risks,
    )


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


def annualized_volatility(bars: list[Bar]) -> float:
    closes = [bar.close for bar in sorted(bars, key=lambda item: item.trade_date)]
    returns = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])]
    if len(returns) < 2:
        return 1.0
    mean = fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return max(0.0001, math.sqrt(variance * 252))
