from __future__ import annotations

import math
from statistics import fmean, median, pstdev
from typing import Any, Callable, Iterable


HORIZONS = (5, 10, 20)


def return_summary(values: Iterable[float]) -> dict[str, Any]:
    items = [float(value) for value in values]
    gains = [value for value in items if value > 0]
    losses = [value for value in items if value < 0]
    return {
        "sample_count": len(items),
        "win_rate": None if not items else sum(value > 0 for value in items) / len(items),
        "average_return": None if not items else fmean(items),
        "median_return": None if not items else median(items),
        "profit_loss_ratio": (
            None if not gains or not losses
            else fmean(gains) / abs(fmean(losses))
        ),
    }


def performance_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    adverse = [float(row["maximum_adverse_excursion"]) for row in items
               if row.get("maximum_adverse_excursion") is not None]
    return {
        "rows": len(items),
        "maximum_adverse_excursion": {
            "available": len(adverse),
            "mean": None if not adverse else fmean(adverse),
            "median": None if not adverse else median(adverse),
            "worst": None if not adverse else min(adverse),
        },
        "returns": {
            str(horizon): return_summary(
                row[f"return_{horizon}"] for row in items
                if row.get(f"return_{horizon}") is not None
            ) for horizon in HORIZONS
        },
    }


def feature_group_summary(
    groups: dict[str, list[dict[str, Any]]],
    fields: Iterable[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {"group_sizes": {key: len(value) for key, value in groups.items()}}
    effects = []
    for field in fields:
        entry = {}
        values_by_group = {}
        for name, rows in groups.items():
            values = [float(row[field]) for row in rows if row.get(field) is not None]
            values_by_group[name] = values
            entry[name] = {
                "available": len(values),
                "mean": None if not values else fmean(values),
                "median": None if not values else median(values),
            }
        profit = values_by_group.get("profit", [])
        loss = values_by_group.get("loss", [])
        effect = None
        if len(profit) > 1 and len(loss) > 1:
            pooled = math.sqrt((pstdev(profit) ** 2 + pstdev(loss) ** 2) / 2)
            if pooled > 0:
                effect = (fmean(profit) - fmean(loss)) / pooled
        entry["profit_minus_loss_standardized"] = effect
        if effect is not None:
            effects.append({"field": field, "standardized_difference": effect})
        result[field] = entry
    result["profit_loss_effect_ranking"] = sorted(
        effects, key=lambda item: abs(item["standardized_difference"]), reverse=True
    )
    return result


def outcome_groups(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    items = [row for row in rows if row.get("return_20") is not None]
    ordered = sorted(items, key=lambda row: float(row["return_20"]))
    tail = max(1, math.ceil(len(ordered) * 0.10))
    return {
        "profit": [row for row in items if float(row["return_20"]) > 0],
        "loss": [row for row in items if float(row["return_20"]) <= 0],
        "top_10pct": ordered[-tail:],
        "bottom_10pct": ordered[:tail],
    }


def bucket_performance(
    rows: Iterable[dict[str, Any]],
    classifier: Callable[[dict[str, Any]], str | None],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        label = classifier(row)
        if label is not None:
            groups.setdefault(label, []).append(row)
    return {label: performance_summary(values) for label, values in groups.items()}


def delay_distribution(rows: Iterable[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [int(row[field]) for row in rows if row.get(field) is not None]
    count = len(values)
    return {
        "sample_count": count,
        "mean": None if not values else fmean(values),
        "median": None if not values else median(values),
        "proportions": {
            "0": None if not count else sum(value == 0 for value in values) / count,
            "1": None if not count else sum(value == 1 for value in values) / count,
            "2": None if not count else sum(value == 2 for value in values) / count,
            "3": None if not count else sum(value == 3 for value in values) / count,
            "4": None if not count else sum(value == 4 for value in values) / count,
            "5_plus": None if not count else sum(value >= 5 for value in values) / count,
        },
    }


__all__ = [
    "HORIZONS", "bucket_performance", "delay_distribution",
    "feature_group_summary", "outcome_groups", "performance_summary",
    "return_summary",
]
