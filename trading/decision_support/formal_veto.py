from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.candidate_veto import CandidateVetoService


FORMAL_VETO_SOURCE = "HISTORICAL_CANDIDATE_VETO"


@dataclass(frozen=True)
class FormalRiskEvent:
    event_id: str
    title: str
    risk_category: str
    event_time: datetime
    data_available_time: datetime
    data_cutoff: datetime

    def point_in_time_ok(self, requested_cutoff: datetime) -> bool:
        return (
            self.event_time <= requested_cutoff
            and self.data_available_time <= requested_cutoff
            and self.data_cutoff <= requested_cutoff
        )


@dataclass(frozen=True)
class FormalRiskInput:
    events: tuple[FormalRiskEvent, ...]
    suspended: bool | None
    delisted: bool | None
    st_status: bool | None
    event_coverage_complete: bool
    security_status_complete: bool
    requested_cutoff: datetime


@dataclass(frozen=True)
class FormalVetoEvaluation:
    result: VetoResult | None
    source: str
    reason: str
    input_complete: bool
    event_count: int


def evaluate_formal_veto(value: FormalRiskInput) -> FormalVetoEvaluation:
    pit_events = tuple(
        event
        for event in value.events
        if event.point_in_time_ok(value.requested_cutoff)
    )
    pit_complete = len(pit_events) == len(value.events)
    input_complete = (
        value.event_coverage_complete
        and value.security_status_complete
        and pit_complete
    )
    titles = [event.title for event in pit_events]
    if value.suspended is True:
        titles.append("停牌")
    if value.delisted is True:
        titles.append("退市")
    if value.st_status is True:
        titles.append("ST风险")
    checked = CandidateVetoService().check_news_list(
        titles,
        source="formal PIT risk events",
    )
    if checked.vetoed:
        result: VetoResult | None = VetoResult(
            veto_triggered=True,
            veto_type=checked.veto_type,
            veto_reason=checked.reason,
            evidence_source=checked.evidence_source,
            evidence_time=value.requested_cutoff.isoformat(),
            confidence=1.0,
            action_override="AVOID",
        )
        reason = checked.reason
    elif input_complete:
        result = VetoResult(
            veto_triggered=False,
            evidence_source="formal PIT risk events",
            evidence_time=value.requested_cutoff.isoformat(),
        )
        reason = "VETO_INPUT_COMPLETE:NO_FORMAL_RISK_MATCH"
    else:
        result = None
        missing = []
        if not value.event_coverage_complete:
            missing.append("EVENT_COVERAGE")
        if not value.security_status_complete:
            missing.append("SECURITY_STATUS")
        if not pit_complete:
            missing.append("PIT_TIME")
        reason = "VETO_INPUT_INCOMPLETE:" + ",".join(missing)
    return FormalVetoEvaluation(
        result=result,
        source=FORMAL_VETO_SOURCE,
        reason=reason,
        input_complete=input_complete,
        event_count=len(pit_events),
    )


__all__ = [
    "FORMAL_VETO_SOURCE",
    "FormalRiskEvent",
    "FormalRiskInput",
    "FormalVetoEvaluation",
    "evaluate_formal_veto",
]
