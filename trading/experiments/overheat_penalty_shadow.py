from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from trading.experiments.technical_overheat_shadow import (
    extreme_spread, quartile_edges, quartile_metrics,
)


@dataclass(frozen=True)
class PenaltyComponent:
    field: str
    median: float
    q75: float
    weight: float


@dataclass(frozen=True)
class PenaltyCalibration:
    expansion: PenaltyComponent
    ma60_slope: PenaltyComponent
    maximum_component_multiplier: float = 2.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _component_from_research(
    rows: list[dict[str, Any]], field: str,
) -> PenaltyComponent:
    edges = quartile_edges(rows, field)
    groups = quartile_metrics(rows, field, edges)
    spread = extreme_spread(groups)
    # Direct research effect magnitude; no grid search or validation feedback.
    weight = 0.0 if spread is None else max(0.0, -float(spread))
    return PenaltyComponent(field=field, median=edges[1], q75=edges[2], weight=weight)


def calibrate_penalty(rows: Iterable[dict[str, Any]]) -> PenaltyCalibration:
    research = list(rows)
    return PenaltyCalibration(
        expansion=_component_from_research(research, "ma20_ma60_expansion"),
        ma60_slope=_component_from_research(research, "ma60_slope_5"),
    )


def component_penalty(value: float, component: PenaltyComponent, cap: float = 2.0) -> float:
    if value < component.q75 or component.weight <= 0:
        return 0.0
    scale = max(component.q75 - component.median, 1e-12)
    multiplier = min(cap, 1.0 + (value - component.q75) / scale)
    return component.weight * multiplier


def shadow_score(row: dict[str, Any], calibration: PenaltyCalibration) -> dict[str, float]:
    expansion_penalty = component_penalty(
        float(row[calibration.expansion.field]), calibration.expansion,
        calibration.maximum_component_multiplier,
    )
    slope_penalty = component_penalty(
        float(row[calibration.ma60_slope.field]), calibration.ma60_slope,
        calibration.maximum_component_multiplier,
    )
    total = expansion_penalty + slope_penalty
    return {
        "original_technical_score": float(row["technical_score"]),
        "expansion_penalty": expansion_penalty,
        "ma60_slope_penalty": slope_penalty,
        "total_penalty": total,
        "shadow_technical_score": float(row["technical_score"]) - total,
    }


def top_fraction(rows: Iterable[dict[str, Any]], field: str, fraction: float) -> list[dict[str, Any]]:
    items = sorted(rows, key=lambda row: float(row[field]), reverse=True)
    count = max(1, round(len(items) * fraction))
    return items[:count]


__all__ = [
    "PenaltyCalibration", "PenaltyComponent", "calibrate_penalty",
    "component_penalty", "shadow_score", "top_fraction",
]
