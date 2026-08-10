from __future__ import annotations

from dataclasses import dataclass

from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.schemas import CapitalFlowRiskFlag


@dataclass(frozen=True)
class VolumeFeatures:
    volume_ratio_5d: float | None
    volume_ratio_20d: float | None
    volume_percentile_20d: float | None
    volume_percentile_60d: float | None
    consecutive_expansion_days: int
    consecutive_contraction_days: int
    risk_flags: tuple[CapitalFlowRiskFlag, ...]


def _valid(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None and value > 0]


def _ratio(current: float | None, history: list[float | None], window: int) -> float | None:
    prior = _valid(history[-window:])
    if current is None or current <= 0 or len(prior) < window:
        return None
    return current / (sum(prior) / len(prior))


def _percentile(current: float | None, history: list[float | None], window: int) -> float | None:
    prior = _valid(history[-window:])
    if current is None or current <= 0 or len(prior) < window:
        return None
    return sum(value <= current for value in prior) / len(prior)


def calculate_volume_features(bars: list[MarketBar]) -> VolumeFeatures:
    ordered = sorted(bars, key=lambda item: item.event_time)
    if not ordered:
        return VolumeFeatures(None, None, None, None, 0, 0, (CapitalFlowRiskFlag.DATA_GAP,))
    current = ordered[-1].volume
    history = [bar.volume for bar in ordered[:-1]]
    ratio_5 = _ratio(current, history, 5)
    ratio_20 = _ratio(current, history, 20)
    percentile_20 = _percentile(current, history, 20)
    percentile_60 = _percentile(current, history, 60)
    sequence = [bar.volume for bar in ordered if bar.volume is not None and bar.volume > 0]
    expansion = contraction = 0
    for left, right in reversed(list(zip(sequence[:-1], sequence[1:]))):
        if right > left:
            if contraction:
                break
            expansion += 1
        elif right < left:
            if expansion:
                break
            contraction += 1
        else:
            break
    flags: set[CapitalFlowRiskFlag] = set()
    if ratio_20 is None or percentile_60 is None:
        flags.add(CapitalFlowRiskFlag.INSUFFICIENT_HISTORY)
    if ratio_20 is not None and ratio_20 >= 3:
        flags.add(CapitalFlowRiskFlag.EXTREME_VOLUME_SPIKE)
    return VolumeFeatures(
        ratio_5,
        ratio_20,
        percentile_20,
        percentile_60,
        expansion,
        contraction,
        tuple(sorted(flags)),
    )


__all__ = ["VolumeFeatures", "calculate_volume_features"]
