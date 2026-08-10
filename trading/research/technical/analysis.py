from __future__ import annotations

import math
from statistics import fmean

from trading.schemas import Bar, Signal


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
    # A修复：trend 加乖离惩罚（涨太急不再满分）
    #   乖离率 = sma20/sma60 - 1；>20% 时 trend 线性降分（2026-08-04 哈药教训）
    deviation = sma20 / sma60 - 1
    trend = max(-1.0, min(1.0, (sma20 / sma60 - 1) * 12))
    if deviation > 0.20:
        # 乖离超20%：每多10个百分点，trend 减 0.5（封顶 -1.0）
        trend = max(-1.0, trend - (deviation - 0.20) * 5.0)
    momentum = max(-1.0, min(1.0, (ema12 / ema26 - 1) * 25))
    rsi_score = max(-1.0, min(1.0, (50 - abs(rsi14 - 50)) / 50))
    # B修复：RSI 超买惩罚线从 75 降到 70（2026-08-04 哈药 RSI74.4 教训）
    if rsi14 > 70:
        rsi_score = -0.5
    elif rsi14 < 25:
        rsi_score = 0.25
    score = max(-1.0, min(1.0, trend * 0.5 + momentum * 0.35 + rsi_score * 0.15))
    risks: list[str] = []
    if rsi14 > 70:
        risks.append("RSI is overbought; chasing risk is elevated")
    if deviation > 0.20:
        risks.append(f"Bias from SMA20 is high ({deviation:.1%}); mean-reversion risk")
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


def annualized_volatility(bars: list[Bar]) -> float:
    closes = [bar.close for bar in sorted(bars, key=lambda item: item.trade_date)]
    returns = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])]
    if len(returns) < 2:
        return 1.0
    mean = fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return max(0.0001, math.sqrt(variance * 252))
