from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from trading.schemas import AnalysisMode


@dataclass(frozen=True)
class FundamentalModePolicy:
    mode: AnalysisMode
    auto_fetch: bool
    strict_point_in_time: bool
    persist_factor: bool
    shadow_mode: bool
    allow_missing_disclosure_reference: bool
    include_conflicts_as_reference: bool


def policy_for(mode: AnalysisMode) -> FundamentalModePolicy:
    policies = {
        AnalysisMode.SCREENING: FundamentalModePolicy(
            mode=AnalysisMode.SCREENING,
            auto_fetch=False,
            strict_point_in_time=False,
            persist_factor=False,
            shadow_mode=True,
            allow_missing_disclosure_reference=True,
            include_conflicts_as_reference=False,
        ),
        AnalysisMode.RESEARCH: FundamentalModePolicy(
            mode=AnalysisMode.RESEARCH,
            auto_fetch=True,
            strict_point_in_time=False,
            persist_factor=True,
            shadow_mode=True,
            allow_missing_disclosure_reference=True,
            include_conflicts_as_reference=True,
        ),
        AnalysisMode.DECISION: FundamentalModePolicy(
            mode=AnalysisMode.DECISION,
            auto_fetch=True,
            strict_point_in_time=True,
            persist_factor=True,
            shadow_mode=False,
            allow_missing_disclosure_reference=False,
            include_conflicts_as_reference=False,
        ),
    }
    return policies[mode]


def stale_days(period_type: str | None) -> int:
    if period_type == "ANNUAL":
        return settings.fundamental_stale_annual_days
    if period_type == "SEMIANNUAL":
        return settings.fundamental_stale_semiannual_days
    return settings.fundamental_stale_quarterly_days


__all__ = ["FundamentalModePolicy", "policy_for", "stale_days"]
