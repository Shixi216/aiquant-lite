from __future__ import annotations

from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.schemas import (
    CapitalFlowRiskFlag,
    PriceVolumeState,
)


def classify_price_volume(
    bars: list[MarketBar],
    *,
    price_change_threshold: float,
    volume_change_threshold: float,
) -> tuple[PriceVolumeState, tuple[CapitalFlowRiskFlag, ...]]:
    ordered = sorted(bars, key=lambda item: item.event_time)
    if len(ordered) < 2:
        return PriceVolumeState.UNKNOWN, (CapitalFlowRiskFlag.INSUFFICIENT_HISTORY,)
    current, previous = ordered[-1], ordered[-2]
    if (
        current.close is None
        or previous.close is None
        or previous.close <= 0
        or current.volume is None
        or previous.volume is None
        or previous.volume <= 0
    ):
        return PriceVolumeState.UNKNOWN, (CapitalFlowRiskFlag.DATA_GAP,)
    price_change = current.close / previous.close - 1
    volume_change = current.volume / previous.volume - 1
    price = (
        "UP"
        if price_change >= price_change_threshold
        else "DOWN"
        if price_change <= -price_change_threshold
        else "FLAT"
    )
    volume = (
        "UP"
        if volume_change >= volume_change_threshold
        else "DOWN"
        if volume_change <= -volume_change_threshold
        else "FLAT"
    )
    mapping = {
        ("UP", "UP"): PriceVolumeState.PRICE_UP_VOLUME_UP,
        ("UP", "DOWN"): PriceVolumeState.PRICE_UP_VOLUME_DOWN,
        ("DOWN", "UP"): PriceVolumeState.PRICE_DOWN_VOLUME_UP,
        ("DOWN", "DOWN"): PriceVolumeState.PRICE_DOWN_VOLUME_DOWN,
        ("FLAT", "UP"): PriceVolumeState.PRICE_FLAT_VOLUME_UP,
        ("FLAT", "DOWN"): PriceVolumeState.PRICE_FLAT_VOLUME_DOWN,
    }
    state = mapping.get((price, volume), PriceVolumeState.PRICE_VOLUME_NEUTRAL)
    flags: set[CapitalFlowRiskFlag] = set()
    if state == PriceVolumeState.PRICE_UP_VOLUME_DOWN:
        flags.add(CapitalFlowRiskFlag.PRICE_UP_VOLUME_DOWN)
    if state == PriceVolumeState.PRICE_DOWN_VOLUME_UP:
        flags.update(
            {
                CapitalFlowRiskFlag.PRICE_DOWN_VOLUME_UP,
                CapitalFlowRiskFlag.POSSIBLE_DISTRIBUTION,
            }
        )
    return state, tuple(sorted(flags))


__all__ = ["classify_price_volume"]
