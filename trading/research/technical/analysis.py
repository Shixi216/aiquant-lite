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


def _ema_series(values: list[float], period: int) -> list[float]:
    """返回完整 EMA 序列，供 MACD 展示计算 DEA/柱。"""
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _kdj(bars: list[Bar], period: int = 9) -> tuple[float, float, bool]:
    """计算 KDJ，并识别从 70 上方形成的 K/D 向下死叉。"""
    k = 50.0
    d = 50.0
    previous_k = k
    previous_d = d
    crossed_down = False
    for index, bar in enumerate(bars):
        start = max(0, index - period + 1)
        window = bars[start : index + 1]
        lowest = min(item.low for item in window)
        highest = max(item.high for item in window)
        rsv = 50.0 if highest == lowest else (bar.close - lowest) / (highest - lowest) * 100
        previous_k, previous_d = k, d
        k = previous_k * 2 / 3 + rsv / 3
        d = previous_d * 2 / 3 + k / 3
        crossed_down = previous_k >= previous_d and previous_k > 70 and k < d
    return k, d, crossed_down


def _rsi(closes: list[float], period: int = 14) -> float:
    """Wilder 平滑 RSI：前 period 根简单均值作种子，其后全样本指数平滑。

    旧实现只取最近 14 根简单平均，与主流平台口径偏差明显
    （盈新发展 000620：旧口径 61.3，标准口径约 53）。
    样本不足 period 时退化为简单平均。
    """
    changes = [b - a for a, b in zip(closes[:-1], closes[1:])]
    if not changes:
        return 50.0
    if len(changes) < period:
        gains = fmean(max(change, 0) for change in changes)
        losses = fmean(max(-change, 0) for change in changes)
        if losses == 0:
            return 100.0
        return 100 - 100 / (1 + gains / losses)
    gains = fmean(max(change, 0) for change in changes[:period])
    losses = fmean(max(-change, 0) for change in changes[:period])
    for change in changes[period:]:
        gains = (gains * (period - 1) + max(change, 0)) / period
        losses = (losses * (period - 1) + max(-change, 0)) / period
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def _detect_market_regime(index_closes: list[float], period: int = 20) -> str:
    """由大盘指数收盘序列判定市况。

    规则（20日窗口）：
      涨幅 >= +5%  -> RISING（单边上涨，过热惩罚减半——会稽山/妖股教训）
      跌幅 <= -5%  -> FALLING
      其余         -> SIDEWAYS（震荡市，过热惩罚全额生效——A/B回放实证有效区间）
    样本不足返回 UNKNOWN（调用方按 None 处理，维持现状）。
    """
    if len(index_closes) < period + 1:
        return "UNKNOWN"
    window_ret = index_closes[-1] / index_closes[-period - 1] - 1
    if window_ret >= 0.05:
        return "RISING"
    if window_ret <= -0.05:
        return "FALLING"
    return "SIDEWAYS"


def technical_signal(bars: list[Bar], market_regime: str | None = None) -> Signal:
    """个股技术信号。

    market_regime：市况（RISING/SIDEWAYS/FALLING/UNKNOWN/None）。
      - None/UNKNOWN：无差别过热惩罚（历史默认行为，A/B回放基线）。
      - RISING：过热惩罚减半（0.5×）——单边上涨市中超买≠卖点。
      - SIDEWAYS：全额惩罚——震荡市均值回归风险真实存在。
      - FALLING：全额惩罚（下跌市本就不该追高）。
    依据：overheat A/B 两年回放（2026-08-10）——惩罚仅 SIDEWAYS 有效
    （top10%胜率保持 0.74 vs 0.66），RISING/FALLING 无效甚至拖累。
    """
    # 过热惩罚强度系数
    penalty_scale = 0.5 if market_regime == "RISING" else 1.0
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    closes = [bar.close for bar in ordered]
    ma5 = fmean(closes[-5:])
    ma10 = fmean(closes[-10:])
    sma20 = fmean(closes[-20:])
    sma60 = fmean(closes[-60:]) if len(closes) >= 60 else fmean(closes)
    ma120 = fmean(closes[-120:]) if len(closes) >= 120 else None
    ma250 = fmean(closes[-250:]) if len(closes) >= 250 else None
    ema12 = _ema(closes[-60:], 12)
    ema26 = _ema(closes[-60:], 26)
    rsi14 = _rsi(closes)
    # A修复：trend 加乖离惩罚（涨太急不再满分）
    #   乖离率 = sma20/sma60 - 1；>20% 时 trend 线性降分（2026-08-04 哈药教训）
    deviation = sma20 / sma60 - 1
    trend = max(-1.0, min(1.0, (sma20 / sma60 - 1) * 12))
    if deviation > 0.20:
        # 乖离超20%：每多10个百分点，trend 减 0.5×penalty_scale（封顶 -1.0）
        # 市况分层（2026-09-27）：RISING 市减半，SIDEWAYS/FALLING 全额
        trend = max(-1.0, trend - (deviation - 0.20) * 5.0 * penalty_scale)
    evidence: list[str] = []
    if ma120 is None:
        evidence.append(f"MA120样本不足（需120根，当前{len(closes)}根）")
    else:
        distance120 = closes[-1] / ma120 - 1
        evidence.append(f"距MA120({ma120:.2f}) {distance120:+.1%}")
    if ma250 is None:
        evidence.append(f"MA250样本不足（需250根，当前{len(closes)}根）")
    else:
        distance250 = closes[-1] / ma250 - 1
        evidence.append(f"距MA250({ma250:.2f}) {distance250:+.1%}")
    if ma120 is not None and ma250 is not None and closes[-1] < ma120 and closes[-1] < ma250:
        evidence.append(
            "长周期均线下方深度压制"
            f"（距MA120 {closes[-1] / ma120 - 1:+.1%}/"
            f"距MA250 {closes[-1] / ma250 - 1:+.1%}）"
        )
    if closes[-1] < ma5 and closes[-1] < ma10 and ma5 > sma20:
        trend = min(trend, -0.3)
        evidence.append(
            f"短期均线反压（MA5={ma5:.2f}/MA10={ma10:.2f} > 现价{closes[-1]:.2f}）"
        )
    momentum = max(-1.0, min(1.0, (ema12 / ema26 - 1) * 25))
    recent = ordered[-10:]
    recent_high = max(bar.high for bar in recent)
    drawdown = (recent_high - closes[-1]) / recent_high if recent_high else 0.0
    has_limit_down = any(
        # 2026-09-28 L3修复：防 previous.close=0 异常数据除零
        (current.close / previous.close - 1 <= -0.095)
        if previous.close else False
        for previous, current in zip(recent[:-1], recent[1:])
    )
    if drawdown >= 0.15 and has_limit_down:
        momentum = min(momentum, -0.5)
        evidence.append(f"高位回撤{drawdown:.1%}+跌停，顶部反转特征")
    rsi_score = max(-1.0, min(1.0, (50 - abs(rsi14 - 50)) / 50))
    # B修复：RSI 超买惩罚线从 75 降到 70（2026-08-04 哈药 RSI74.4 教训）
    # 市况分层（2026-09-27）：RISING 市惩罚减半（-0.5→-0.25），SIDEWAYS/FALLING 全额
    if rsi14 > 70:
        rsi_score = -0.5 * penalty_scale
    elif rsi14 < 25:
        rsi_score = 0.25
    k_value, d_value, kdj_crossed_down = _kdj(ordered)
    if kdj_crossed_down:
        evidence.append(f"KDJ死叉（K={k_value:.1f}<D={d_value:.1f}）")
    macd_closes = closes[-60:]
    ema12_series = _ema_series(macd_closes, 12)
    ema26_series = _ema_series(macd_closes, 26)
    dif_series = [left - right for left, right in zip(ema12_series, ema26_series)]
    dif = dif_series[-1]
    dea = _ema(dif_series, 9)
    histogram = dif - dea
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
            f"close={closes[-1]:.2f}, MA5={ma5:.2f}, MA10={ma10:.2f}, "
            f"SMA20={sma20:.2f}, SMA60={sma60:.2f}, "
            + (
                f"MA120={ma120:.2f}, "
                if ma120 is not None
                else "MA120=N/A(样本不足), "
            )
            + (
                f"MA250={ma250:.2f}, "
                if ma250 is not None
                else "MA250=N/A(样本不足), "
            )
            + f"DIF={dif:.3f}(EMA差), DEA={dea:.3f}, 柱={histogram:.3f}, RSI14={rsi14:.1f}"
        ),
        evidence=[
            *evidence,
            *[f"bar:{bar.trade_date.isoformat()}:{bar.close:.4f}" for bar in ordered[-5:]],
        ],
        risks=risks,
    )


def annualized_volatility(bars: list[Bar]) -> float:
    closes = [bar.close for bar in sorted(bars, key=lambda item: item.trade_date)]
    # 2026-09-28 L3修复：防异常数据 close<=0 导致 log 除零/域错误
    returns = [
        math.log(b / a)
        for a, b in zip(closes[:-1], closes[1:])
        if a > 0 and b > 0
    ]
    if len(returns) < 2:
        return 1.0
    mean = fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return max(0.0001, math.sqrt(variance * 252))
