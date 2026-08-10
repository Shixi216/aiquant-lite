from __future__ import annotations

from datetime import datetime

from trading.research.orchestration.models import (
    AVAILABILITY_WEIGHTS,
    DECISION_EXCLUDED_AVAILABILITY,
)
from trading.research.orchestration.schemas import (
    FactorAvailabilityStatus,
    FactorView,
)
from trading.schemas import AnalysisMode


_CONFLICT_MARKERS = ("CONFLICT", "CONTRADICT")
_STALE_MARKERS = ("STALE",)
_UNVERIFIED_MARKERS = (
    "UNVERIFIED",
    "SINGLE_SOURCE",
    "TITLE_ONLY",
)
_PARTIAL_MARKERS = (
    "DATA_GAP",
    "MISSING",
    "PARTIAL",
    "INSUFFICIENT",
    "INCOMPLETE",
)
_MODE_MARKERS = ("MODE_RESTRICTION",)


def classify_availability(
    factor: FactorView,
    *,
    analysis_mode: AnalysisMode,
    requested_cutoff: datetime,
) -> tuple[FactorAvailabilityStatus, str]:
    flags = {flag.upper() for flag in factor.risk_flags}
    if factor.data_cutoff > requested_cutoff:
        return (
            FactorAvailabilityStatus.FUTURE_DATA_REJECTED,
            "factor data_cutoff is later than the requested cutoff",
        )
    if any(marker in flag for flag in flags for marker in _MODE_MARKERS):
        return (
            FactorAvailabilityStatus.MODE_RESTRICTED,
            "factor is restricted in the selected analysis mode",
        )
    if any(marker in flag for flag in flags for marker in _CONFLICT_MARKERS):
        return (
            FactorAvailabilityStatus.CONFLICT,
            "factor evidence is conflicting and has no directional contribution",
        )
    if any(marker in flag for flag in flags for marker in _STALE_MARKERS):
        suffix = (
            " and is excluded from DECISION"
            if analysis_mode == AnalysisMode.DECISION
            else " and receives a reduced availability weight"
        )
        return FactorAvailabilityStatus.STALE, "factor is stale" + suffix
    if any(marker in flag for flag in flags for marker in _UNVERIFIED_MARKERS):
        return (
            FactorAvailabilityStatus.UNVERIFIED,
            "factor evidence is present but not independently verified",
        )
    if any(marker in flag for flag in flags for marker in _PARTIAL_MARKERS):
        return (
            FactorAvailabilityStatus.PARTIAL,
            "factor is usable with explicitly recorded data gaps",
        )
    return FactorAvailabilityStatus.AVAILABLE, "factor is available"


def availability_weight(
    status: FactorAvailabilityStatus,
    *,
    analysis_mode: AnalysisMode,
) -> float:
    if (
        analysis_mode == AnalysisMode.DECISION
        and status.value in DECISION_EXCLUDED_AVAILABILITY
    ):
        return 0.0
    return AVAILABILITY_WEIGHTS[status.value]


def directional_score(factor: FactorView) -> float:
    if factor.availability_status == FactorAvailabilityStatus.CONFLICT:
        return 0.0
    return factor.score


__all__ = [
    "availability_weight",
    "classify_availability",
    "directional_score",
]
