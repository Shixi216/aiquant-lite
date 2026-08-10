from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MarketState:
    trade_date: date
    csi300_close: float
    csi300_ma20: float | None
    csi300_ma60: float | None
    csi300_return20: float | None
    advancers: int
    decliners: int
    above_ma20_ratio: float | None
    amount_ratio20: float | None


def condition_flags(state: MarketState) -> dict[str, bool | None]:
    return {
        "csi300_above_ma20": (
            None if state.csi300_ma20 is None
            else state.csi300_close > state.csi300_ma20
        ),
        "csi300_above_ma60": (
            None if state.csi300_ma60 is None
            else state.csi300_close > state.csi300_ma60
        ),
        "ma20_above_ma60": (
            None if state.csi300_ma20 is None or state.csi300_ma60 is None
            else state.csi300_ma20 > state.csi300_ma60
        ),
        "csi300_return20_positive": (
            None if state.csi300_return20 is None
            else state.csi300_return20 > 0
        ),
        "advancers_exceed_decliners": state.advancers > state.decliners,
        "above_ma20_majority": (
            None if state.above_ma20_ratio is None
            else state.above_ma20_ratio > .5
        ),
        "amount_above_prior20_average": (
            None if state.amount_ratio20 is None
            else state.amount_ratio20 > 1
        ),
    }


def chronological_split(
    signal_dates: list[date],
    *,
    research_fraction: float = .60,
    embargo_dates: int = 20,
) -> tuple[tuple[date, ...], tuple[date, ...], tuple[date, ...]]:
    ordered = sorted(set(signal_dates))
    if not ordered:
        return (), (), ()
    split = max(1, min(len(ordered), int(len(ordered) * research_fraction)))
    research = tuple(ordered[:split])
    embargo = tuple(ordered[split:split + embargo_dates])
    validation = tuple(ordered[split + embargo_dates:])
    return research, embargo, validation


__all__ = ["MarketState", "chronological_split", "condition_flags"]
