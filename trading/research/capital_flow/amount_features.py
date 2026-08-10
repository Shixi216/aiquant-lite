from __future__ import annotations

from dataclasses import dataclass

from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.schemas import CapitalFlowRiskFlag


@dataclass(frozen=True)
class AmountFeatures:
    amount_ratio_5d: float | None
    amount_ratio_20d: float | None
    amount_percentile_20d: float | None
    amount_percentile_60d: float | None
    average_amount_5d: float | None
    average_amount_20d: float | None
    risk_flags: tuple[CapitalFlowRiskFlag, ...]


def _window(history: list[float | None], length: int) -> list[float]:
    values = [value for value in history[-length:] if value is not None and value > 0]
    return values if len(values) == length else []


def calculate_amount_features(bars: list[MarketBar]) -> AmountFeatures:
    ordered = sorted(bars, key=lambda item: item.event_time)
    if not ordered:
        return AmountFeatures(None, None, None, None, None, None, (CapitalFlowRiskFlag.DATA_GAP,))
    current = ordered[-1].amount
    history = [bar.amount for bar in ordered[:-1]]
    values5 = _window(history, 5)
    values20 = _window(history, 20)
    values60 = _window(history, 60)
    average5 = sum(values5) / 5 if values5 else None
    average20 = sum(values20) / 20 if values20 else None
    ratio5 = current / average5 if current is not None and current > 0 and average5 else None
    ratio20 = current / average20 if current is not None and current > 0 and average20 else None
    percentile20 = (
        sum(value <= current for value in values20) / 20
        if current is not None and current > 0 and values20
        else None
    )
    percentile60 = (
        sum(value <= current for value in values60) / 60
        if current is not None and current > 0 and values60
        else None
    )
    flags: set[CapitalFlowRiskFlag] = set()
    if ratio20 is None or percentile60 is None:
        flags.add(CapitalFlowRiskFlag.INSUFFICIENT_HISTORY)
    if ratio20 is not None and ratio20 >= 3:
        flags.add(CapitalFlowRiskFlag.EXTREME_AMOUNT_SPIKE)
    return AmountFeatures(
        ratio5,
        ratio20,
        percentile20,
        percentile60,
        average5,
        average20,
        tuple(sorted(flags)),
    )


__all__ = ["AmountFeatures", "calculate_amount_features"]
