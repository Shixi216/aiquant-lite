from __future__ import annotations

from dataclasses import dataclass

from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.schemas import CapitalFlowRiskFlag


@dataclass(frozen=True)
class TurnoverFeatures:
    turnover_rate: float | None
    turnover_percentile_20d: float | None
    turnover_percentile_60d: float | None
    turnover_change: float | None
    consecutive_high_turnover_days: int
    risk_flags: tuple[CapitalFlowRiskFlag, ...]


def trusted_turnover(bar: MarketBar) -> float | None:
    if bar.turnover_rate is not None:
        return bar.turnover_rate
    if bar.volume is not None and bar.float_shares is not None and bar.float_shares > 0:
        return bar.volume / bar.float_shares
    return None


def calculate_turnover_features(
    bars: list[MarketBar],
    *,
    high_threshold: float,
) -> TurnoverFeatures:
    ordered = sorted(bars, key=lambda item: item.event_time)
    values = [trusted_turnover(bar) for bar in ordered]
    if not values:
        values = [None]
    current = values[-1]
    history = [value for value in values[:-1] if value is not None]

    def percentile(window: int) -> float | None:
        prior = history[-window:]
        if current is None or len(prior) < window:
            return None
        return sum(value <= current for value in prior) / len(prior)

    consecutive = 0
    for value in reversed(values):
        if value is not None and value >= high_threshold:
            consecutive += 1
        else:
            break
    flags: set[CapitalFlowRiskFlag] = set()
    if current is None:
        flags.update(
            {
                CapitalFlowRiskFlag.TURNOVER_DATA_MISSING,
                CapitalFlowRiskFlag.FLOAT_SHARES_MISSING,
            }
        )
    elif current >= high_threshold:
        flags.add(CapitalFlowRiskFlag.HIGH_TURNOVER)
    return TurnoverFeatures(
        turnover_rate=current,
        turnover_percentile_20d=percentile(20),
        turnover_percentile_60d=percentile(60),
        turnover_change=(
            current - history[-1]
            if current is not None and history
            else None
        ),
        consecutive_high_turnover_days=consecutive,
        risk_flags=tuple(sorted(flags)),
    )


__all__ = ["TurnoverFeatures", "calculate_turnover_features", "trusted_turnover"]
