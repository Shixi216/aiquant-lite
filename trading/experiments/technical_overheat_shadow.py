from __future__ import annotations

from statistics import fmean, median
from typing import Any, Iterable


HORIZONS = (5, 10, 20)


def chronological_segments(
    rows: Iterable[dict[str, Any]],
    *,
    research_fraction: float = 0.60,
    embargo_dates: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    items = list(rows)
    dates = sorted({row["signal_date"] for row in items})
    cut = max(1, int(len(dates) * research_fraction))
    research_dates = set(dates[:cut])
    embargo = set(dates[cut:cut + embargo_dates])
    validation_dates = set(dates[cut + embargo_dates:])
    return {
        "research": [row for row in items if row["signal_date"] in research_dates],
        "embargo": [row for row in items if row["signal_date"] in embargo],
        "validation": [row for row in items if row["signal_date"] in validation_dates],
    }


def quantile(values: Iterable[float], proportion: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile needs at least one value")
    return ordered[round((len(ordered) - 1) * proportion)]


def quartile_edges(rows: Iterable[dict[str, Any]], field: str) -> tuple[float, float, float]:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return tuple(quantile(values, value) for value in (.25, .50, .75))


def quartile_label(value: float | None, edges: tuple[float, float, float]) -> str | None:
    if value is None:
        return None
    if value < edges[0]:
        return "Q1"
    if value < edges[1]:
        return "Q2"
    if value < edges[2]:
        return "Q3"
    return "Q4"


def group_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    result: dict[str, Any] = {"sample_count": len(items), "returns": {}}
    for horizon in HORIZONS:
        values = [float(row[f"return_{horizon}"]) for row in items
                  if row.get(f"return_{horizon}") is not None]
        result["returns"][str(horizon)] = {
            "sample_count": len(values),
            "win_rate": None if not values else sum(value > 0 for value in values) / len(values),
            "average_return": None if not values else fmean(values),
            "median_return": None if not values else median(values),
        }
    for field in ("maximum_favorable_excursion", "maximum_adverse_excursion"):
        values = [float(row[field]) for row in items if row.get(field) is not None]
        result[field] = {
            "available": len(values),
            "mean": None if not values else fmean(values),
            "median": None if not values else median(values),
            "extreme": None if not values else (
                max(values) if field == "maximum_favorable_excursion" else min(values)
            ),
        }
    return result


def quartile_metrics(
    rows: Iterable[dict[str, Any]], field: str,
    edges: tuple[float, float, float],
) -> dict[str, Any]:
    groups = {label: [] for label in ("Q1", "Q2", "Q3", "Q4")}
    for row in rows:
        label = quartile_label(row.get(field), edges)
        if label is not None:
            groups[label].append(row)
    return {label: group_metrics(values) for label, values in groups.items()}


def extreme_spread(groups: dict[str, Any], horizon: str = "20") -> float | None:
    low = groups["Q1"]["returns"][horizon]["average_return"]
    high = groups["Q4"]["returns"][horizon]["average_return"]
    return None if low is None or high is None else high - low


__all__ = [
    "HORIZONS", "chronological_segments", "extreme_spread", "group_metrics",
    "quartile_edges", "quartile_label", "quartile_metrics", "quantile",
]
