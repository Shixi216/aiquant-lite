from __future__ import annotations

import math
from statistics import fmean
from typing import Any

import pandas as pd


def _numbers(value: Any) -> list[float]:
    if value is None or not hasattr(value, "__iter__"):
        return []
    output: list[float] = []
    for item in value:
        if item is None or bool(getattr(item, "mask", False)):
            continue
        if not pd.isna(item):
            output.append(float(item))
    return output


def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    changes = [
        right - left
        for left, right in zip(
            closes[-period - 1 : -1],
            closes[-period:],
            strict=True,
        )
    ]
    gains = fmean(max(change, 0.0) for change in changes)
    losses = fmean(max(-change, 0.0) for change in changes)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def _volatility(closes: list[float], window: int = 20) -> float | None:
    selected = closes[-(window + 1) :]
    if len(selected) < 3 or any(value <= 0 for value in selected):
        return None
    returns = [
        math.log(right / left)
        for left, right in zip(selected[:-1], selected[1:], strict=True)
    ]
    mean = fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (
        len(returns) - 1
    )
    return math.sqrt(max(0.0, variance) * 252)


def _streak(closes: list[float], *, up: bool) -> int:
    count = 0
    for left, right in reversed(
        list(zip(closes[:-1], closes[1:], strict=True))
    ):
        if (right > left) if up else (right < left):
            count += 1
        else:
            break
    return count


def _technical_row(row: Any) -> dict[str, Any]:
    closes = _numbers(row.history_closes)
    highs = _numbers(row.history_highs)
    lows = _numbers(row.history_lows)
    current = (
        float(row.current_price)
        if row.current_price is not None and not pd.isna(row.current_price)
        else None
    )
    if closes and current is not None:
        closes[-1] = current
    sma = {
        length: fmean(closes[-length:]) if len(closes) >= length else None
        for length in (5, 10, 20, 60)
    }
    rsi14 = _rsi(closes)
    ema12 = _ema(closes[-60:], 12)
    ema26 = _ema(closes[-60:], 26)
    macd_proxy = (
        None if ema12 is None or ema26 is None else ema12 - ema26
    )
    macd_state = (
        None
        if macd_proxy is None
        else "POSITIVE"
        if macd_proxy > 0
        else "NEGATIVE"
        if macd_proxy < 0
        else "NEUTRAL"
    )
    technical_score = None
    technical_confidence = None
    if len(closes) >= 30 and sma[20] and sma[60] and ema12 and ema26:
        trend = max(-1.0, min(1.0, (sma[20] / sma[60] - 1) * 12))
        momentum = max(-1.0, min(1.0, (ema12 / ema26 - 1) * 25))
        rsi_score = (
            0.0
            if rsi14 is None
            else max(-1.0, min(1.0, (50 - abs(rsi14 - 50)) / 50))
        )
        if rsi14 is not None and rsi14 > 75:
            rsi_score = -0.5
        elif rsi14 is not None and rsi14 < 25:
            rsi_score = 0.25
        technical_score = max(
            -1.0,
            min(1.0, trend * 0.5 + momentum * 0.35 + rsi_score * 0.15),
        )
        technical_confidence = min(0.9, 0.55 + len(closes) / 500)
    prior_high20 = max(highs[-21:-1]) if len(highs) >= 21 else None
    prior_low20 = min(lows[-21:-1]) if len(lows) >= 21 else None
    return {
        "sma5": sma[5],
        "sma10": sma[10],
        "sma20": sma[20],
        "sma60": sma[60],
        "above_sma5": None if current is None or sma[5] is None else current > sma[5],
        "above_sma10": None if current is None or sma[10] is None else current > sma[10],
        "above_sma20": None if current is None or sma[20] is None else current > sma[20],
        "above_sma60": None if current is None or sma[60] is None else current > sma[60],
        "ma_bullish": (
            None
            if any(sma[length] is None for length in (5, 10, 20, 60))
            else sma[5] > sma[10] > sma[20] > sma[60]
        ),
        "ma_bearish": (
            None
            if any(sma[length] is None for length in (5, 10, 20, 60))
            else sma[5] < sma[10] < sma[20] < sma[60]
        ),
        "rsi14": rsi14,
        "macd_proxy": macd_proxy,
        "macd_state": macd_state,
        "breakout_high_20d": (
            None
            if current is None or prior_high20 is None
            else current > prior_high20
        ),
        "breakdown_low_20d": (
            None
            if current is None or prior_low20 is None
            else current < prior_low20
        ),
        "consecutive_up_days": _streak(closes, up=True),
        "consecutive_down_days": _streak(closes, up=False),
        "volatility_20d": _volatility(closes),
        "technical_score": technical_score,
        "technical_confidence": technical_confidence,
    }


def compute_technical_features(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [_technical_row(row) for row in frame.itertuples(index=False)]
    return pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame.from_records(rows)],
        axis=1,
    )


__all__ = ["compute_technical_features"]
