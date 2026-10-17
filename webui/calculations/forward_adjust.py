"""Pure forward-adjustment (QFQ) helpers — identical formula to production.

Production authority:
``data_hub/services/formal_history_data_service.py::materialize_forward_adjusted_bars``

    base_factor = adj_factor at the latest trade date in the window
    ratio[t]    = adj_factor[t] / base_factor
    open/high/low/close *= ratio        (volume / amount untouched)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdjustmentOutcome:
    requested_adjustment_type: str
    effective_adjustment_type: str
    adjustment_fallback: bool
    adjustment_fallback_reason: str | None
    factor_anchor_date: str | None


def base_factor_of(factors: dict[str, float]) -> tuple[str | None, float | None]:
    """ARG_MAX(adj_factor, trade_date): anchor at the latest dated factor."""
    if not factors:
        return None, None
    anchor_date = max(factors)
    return anchor_date, factors[anchor_date]


def ratio_series(
    trade_dates: list[str], factors: dict[str, float], base: float
) -> list[float | None]:
    return [
        None if trade_date not in factors else factors[trade_date] / base
        for trade_date in trade_dates
    ]


def outcome_for(
    requested: str,
    *,
    factor_missing_total: bool,
    factor_missing_partial: bool,
    anchor_date: str | None,
) -> AdjustmentOutcome:
    if requested == "RAW":
        return AdjustmentOutcome("RAW", "RAW", False, None, None)
    if factor_missing_total:
        return AdjustmentOutcome(
            requested, "RAW", True, "missing_adjustment_factors", None
        )
    if factor_missing_partial:
        return AdjustmentOutcome(
            requested, "RAW", True, "factor_window_incomplete", None
        )
    return AdjustmentOutcome(
        requested, "FORWARD_ADJUSTED", False, None, anchor_date
    )


__all__ = [
    "AdjustmentOutcome",
    "base_factor_of",
    "ratio_series",
    "outcome_for",
]
