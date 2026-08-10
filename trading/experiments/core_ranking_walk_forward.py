from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil, sqrt
from statistics import fmean
from typing import Any, Iterable


RANKING_NAMES = ("A", "B", "C")
TOP_FRACTIONS = (0.10, 0.20, 0.30)
HORIZONS = (5, 10, 20)


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_dates: tuple[date, ...]
    embargo_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]


def build_walk_forward_folds(
    dates: Iterable[date],
    *,
    initial_train_fraction: float = 0.30,
    validation_fraction: float = 0.15,
    embargo_size: int = 20,
    minimum_validation_size: int = 20,
) -> list[WalkForwardFold]:
    ordered = sorted(set(dates))
    if len(ordered) < embargo_size + minimum_validation_size + 40:
        raise ValueError("insufficient dates for multi-fold walk-forward")
    initial = max(40, round(len(ordered) * initial_train_fraction))
    validation_size = max(
        minimum_validation_size,
        round(len(ordered) * validation_fraction),
    )
    folds: list[WalkForwardFold] = []
    train_end = initial
    while train_end + embargo_size + minimum_validation_size <= len(ordered):
        validation_start = train_end + embargo_size
        validation_end = min(len(ordered), validation_start + validation_size)
        folds.append(
            WalkForwardFold(
                fold=len(folds) + 1,
                train_dates=tuple(ordered[:train_end]),
                embargo_dates=tuple(ordered[train_end:validation_start]),
                validation_dates=tuple(ordered[validation_start:validation_end]),
            )
        )
        train_end = validation_end
    if len(folds) < 3:
        raise ValueError("walk-forward requires at least three folds")
    return folds


def cross_sectional_percentiles(
    rows: Iterable[dict[str, Any]], field: str,
) -> dict[str, float]:
    items = sorted(
        rows,
        key=lambda row: (float(row[field]), str(row["packet_id"])),
    )
    if not items:
        return {}
    if len(items) == 1:
        return {str(items[0]["packet_id"]): 1.0}
    return {
        str(row["packet_id"]): index / (len(items) - 1)
        for index, row in enumerate(items)
    }


def assign_ranking_scores(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows]
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in values:
        by_date.setdefault(row["signal_date"], []).append(row)
    for items in by_date.values():
        b_rank = cross_sectional_percentiles(items, "b_score")
        fundamental_rank = cross_sectional_percentiles(
            items, "fundamental_score"
        )
        for row in items:
            packet_id = str(row["packet_id"])
            row["rank_score_A"] = float(row["technical_score"])
            row["rank_score_B"] = float(row["b_score"])
            # No optimized weight: equal rank blend fixed before validation.
            row["rank_score_C"] = (
                b_rank[packet_id] + fundamental_rank[packet_id]
            ) / 2
    return values


def select_daily_top_fraction(
    rows: Iterable[dict[str, Any]],
    *,
    ranking: str,
    fraction: float,
    allowed_dates: set[date],
) -> set[str]:
    if ranking not in RANKING_NAMES:
        raise ValueError(f"unknown ranking: {ranking}")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        if row["signal_date"] in allowed_dates:
            by_date.setdefault(row["signal_date"], []).append(row)
    selected: set[str] = set()
    field = f"rank_score_{ranking}"
    for items in by_date.values():
        ordered = sorted(
            items,
            key=lambda row: (-float(row[field]), str(row["packet_id"])),
        )
        count = max(1, ceil(len(ordered) * fraction))
        selected.update(str(row["packet_id"]) for row in ordered[:count])
    return selected


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + end - 1) / 2
        for index in order[start:end]:
            result[index] = average_rank
        start = end
    return result


def spearman(values: Iterable[tuple[float, float]]) -> float | None:
    pairs = [(float(left), float(right)) for left, right in values]
    if len(pairs) < 3:
        return None
    left = _ranks([item[0] for item in pairs])
    right = _ranks([item[1] for item in pairs])
    left_mean, right_mean = fmean(left), fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    left_ss = sum((item - left_mean) ** 2 for item in left)
    right_ss = sum((item - right_mean) ** 2 for item in right)
    denominator = sqrt(left_ss * right_ss)
    return None if denominator <= 1e-12 else numerator / denominator


def factor_information_coefficients(
    rows: Iterable[dict[str, Any]],
    returns_by_packet: dict[str, float],
    fields: Iterable[str],
) -> dict[str, dict[str, float | int | None]]:
    items = list(rows)
    output: dict[str, dict[str, float | int | None]] = {}
    for field in fields:
        pairs = [
            (float(row[field]), returns_by_packet[str(row["packet_id"])])
            for row in items
            if row.get(field) is not None
            and str(row["packet_id"]) in returns_by_packet
        ]
        output[field] = {
            "sample_count": len(pairs),
            "spearman_ic": spearman(pairs),
        }
    return output


__all__ = [
    "HORIZONS",
    "RANKING_NAMES",
    "TOP_FRACTIONS",
    "WalkForwardFold",
    "assign_ranking_scores",
    "build_walk_forward_folds",
    "cross_sectional_percentiles",
    "factor_information_coefficients",
    "select_daily_top_fraction",
    "spearman",
]
