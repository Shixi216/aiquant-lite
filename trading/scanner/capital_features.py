from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _numbers(value: Any) -> list[float]:
    if value is None or not hasattr(value, "__iter__"):
        return []
    output: list[float] = []
    for item in value:
        if item is None or bool(getattr(item, "mask", False)):
            continue
        if not pd.isna(item) and float(item) >= 0:
            output.append(float(item))
    return output


def _ratio(current: float | None, history: list[float], window: int) -> float | None:
    prior = history[-(window + 1) : -1]
    if current is None or current <= 0 or len(prior) < window:
        return None
    valid = [value for value in prior if value > 0]
    if len(valid) != window:
        return None
    return current / (sum(valid) / window)


def _value(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _capital_row(row: Any) -> dict[str, Any]:
    volumes = _numbers(row.history_volumes)
    amounts = _numbers(row.history_amounts)
    current_volume = _value(row.current_volume)
    current_amount = _value(row.amount)
    if volumes and current_volume is not None:
        volumes[-1] = current_volume
    if amounts and current_amount is not None:
        amounts[-1] = current_amount
    volume_ratio_5d = _ratio(current_volume, volumes, 5)
    volume_ratio_20d = _ratio(current_volume, volumes, 20)
    amount_ratio_5d = _ratio(current_amount, amounts, 5)
    amount_ratio_20d = _ratio(current_amount, amounts, 20)
    components = [
        max(-1.0, min(1.0, math.log(value, 2) / 2))
        for value in (volume_ratio_5d, amount_ratio_5d)
        if value is not None and value > 0
    ]
    capital_score = sum(components) / len(components) if components else 0.0
    capital_confidence = min(0.35, 0.1 + len(components) * 0.1)
    previous_volume = volumes[-2] if len(volumes) >= 2 else None
    volume_change = (
        None
        if current_volume is None
        or previous_volume is None
        or previous_volume <= 0
        else current_volume / previous_volume - 1
    )
    price_change = _value(row.change_pct)
    price_volume_state = "UNKNOWN"
    if price_change is not None and volume_change is not None:
        price = "UP" if price_change >= 0.01 else "DOWN" if price_change <= -0.01 else "FLAT"
        volume = "UP" if volume_change >= 0.20 else "DOWN" if volume_change <= -0.20 else "FLAT"
        price_volume_state = f"PRICE_{price}_VOLUME_{volume}"
    return {
        "volume_ratio_5d": volume_ratio_5d,
        "volume_ratio_20d": volume_ratio_20d,
        "amount_ratio_5d": amount_ratio_5d,
        "amount_ratio_20d": amount_ratio_20d,
        "volume_change": volume_change,
        "price_volume_state": price_volume_state,
        "capital_flow_score": capital_score,
        "capital_flow_confidence": capital_confidence,
    }


def compute_capital_features(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [_capital_row(row) for row in frame.itertuples(index=False)]
    output = pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame.from_records(rows)],
        axis=1,
    )
    output["amount_market_percentile"] = (
        output["amount"].rank(method="min", pct=True).fillna(0.0)
    )
    return output


__all__ = ["compute_capital_features"]
