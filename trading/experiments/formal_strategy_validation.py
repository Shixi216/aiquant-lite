from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.candidate_veto import CandidateVetoService
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.price_zones import Bar as ZoneBar
from trading.decision_support.task_context import TaskContext
from trading.experiments.parameter_sensitivity import (
    FutureBar,
    HistoricalParameterObservation,
)
from trading.research.fundamental.analysis import fundamental_signal
from trading.research.fundamental.metrics import calculate_fundamental_metrics
from trading.research.fundamental.models import (
    MarketValuationPoint,
    PointInTimeFinancialRecord,
)
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.research.technical.analysis import technical_signal
from trading.scanner.production_partition import CandidateLayer
from trading.schemas import Bar as TechnicalBar, FundamentalSnapshot


FORMAL_HISTORY_VALIDATION_VERSION = "formal-history-validation-v1"


class FutureDataLeakageError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalBarPoint:
    bar: TechnicalBar
    data_available_time: datetime
    data_cutoff: datetime


@dataclass(frozen=True)
class FormalReplayInput:
    symbol: str
    signal_date: date
    data_cutoff: datetime
    layer: CandidateLayer
    bars: tuple[HistoricalBarPoint, ...]
    financial_records: tuple[PointInTimeFinancialRecord, ...]
    valuation_point: MarketValuationPoint | None
    future_bars: tuple[FutureBar, ...]
    csi300_returns: dict[int, float] = field(default_factory=dict)
    csi300_returns_by_exit_date: dict[date, float] = field(default_factory=dict)
    industry_returns_by_exit_date: dict[date, float] = field(default_factory=dict)
    market_regime: str = "UNKNOWN"
    risk_event_titles: tuple[str, ...] = ()
    risk_event_coverage_complete: bool = False
    suspended: bool = False
    delisted: bool = False
    st_status: bool = False
    historical_universe_complete: bool = False


@dataclass(frozen=True)
class FormalReplayDecision:
    symbol: str
    signal_date: date
    layer: CandidateLayer
    market_regime: str
    technical_score: float
    fundamental_score: float
    formal_score: float
    confidence: float
    action: str
    data_status: str
    execution_status: str
    veto_triggered: bool
    frozen_entry_zone: tuple[float, ...]
    frozen_preferred_zone: tuple[float, ...]
    frozen_stop_loss_price: float | None
    target_position_ratio: float
    recommended_batches: int
    point_in_time_valid: bool
    missing_data: tuple[str, ...]
    observation: HistoricalParameterObservation
    industry_returns_by_exit_date: dict[date, float]


class _NoPositionReader:
    def get_holding(self, local_user_id: str, symbol: str):
        return None


class _NoLivePermission:
    def can_live_trade(self, local_user_id: str) -> bool:
        return False


class _NoTradePlanRepository:
    def create_plan(self, plan):
        raise RuntimeError("formal history replay is read-only")


def _validate_point_in_time(value: FormalReplayInput) -> None:
    if value.data_cutoff.tzinfo is None or value.data_cutoff.utcoffset() is None:
        raise ValueError("data_cutoff must include a timezone")
    for point in value.bars:
        if (
            point.data_available_time > value.data_cutoff
            or point.data_cutoff > value.data_cutoff
            or point.bar.trade_date > value.signal_date
        ):
            raise FutureDataLeakageError(
                f"future market data rejected for {value.symbol}"
            )
    for record in value.financial_records:
        available = record.data_available_time or record.event_time
        if available > value.data_cutoff or record.event_time > value.data_cutoff:
            raise FutureDataLeakageError(
                f"future financial data rejected for {value.symbol}"
            )
    if (
        value.valuation_point is not None
        and value.valuation_point.event_time > value.data_cutoff
    ):
        raise FutureDataLeakageError(
            f"future valuation data rejected for {value.symbol}"
        )


def _snapshot(
    value: FormalReplayInput,
) -> tuple[FundamentalSnapshot | None, list[str]]:
    if not value.financial_records:
        return None, ["PIT_FUNDAMENTAL_MISSING"]
    metrics, missing, _, used = calculate_fundamental_metrics(
        records=list(value.financial_records),
        valuation_point=value.valuation_point,
    )
    available_times = [
        record.data_available_time or record.event_time for record in used
    ]
    announcements = [
        record.announcement_time
        for record in used
        if record.announcement_time is not None
    ]
    snapshot = FundamentalSnapshot(
        as_of=value.signal_date,
        pe_ttm=metrics.pe_ttm,
        pb=metrics.pb,
        roe=metrics.roe,
        revenue_growth=metrics.revenue_growth,
        net_profit_growth=metrics.net_profit_growth,
        debt_ratio=metrics.debt_ratio,
        operating_cash_flow=metrics.operating_cash_flow,
        operating_cash_flow_positive=metrics.operating_cash_flow_positive,
        peg=metrics.peg,
        report_period=metrics.report_period,
        statement_types=metrics.statement_types,
        announcement_time=max(announcements) if announcements else None,
        data_available_time=max(available_times) if available_times else None,
        primary_source=(used[0].primary_source if used else None),
        verification_status=(
            used[0].verification_status.value if used else None
        ),
        source_type=(used[0].source_type if used else None),
        evidence_refs=[record.canonical_record_id for record in used],
    )
    return snapshot, missing


def _veto(value: FormalReplayInput) -> VetoResult:
    titles = list(value.risk_event_titles)
    if value.suspended:
        titles.append("停牌")
    if value.delisted:
        titles.append("退市")
    if value.st_status:
        titles.append("ST风险")
    result = CandidateVetoService().check_news_list(
        titles,
        source="historical PIT risk events",
    )
    return VetoResult(
        veto_triggered=result.vetoed,
        veto_type=result.veto_type,
        veto_reason=result.reason,
        evidence_source=result.evidence_source,
        evidence_time=value.data_cutoff.isoformat(),
        confidence=1.0 if result.vetoed else 0.0,
        action_override="AVOID" if result.vetoed else "",
    )


class FormalStrategyReplayService:
    """Pure research replay over the existing formal production chain."""

    def __init__(self, engine: DecisionEngine | None = None) -> None:
        self.engine = engine or DecisionEngine(
            permission_service=_NoLivePermission(),
            manual_positions=_NoPositionReader(),
            plan_repo=_NoTradePlanRepository(),
        )

    def replay_one(self, value: FormalReplayInput) -> FormalReplayDecision:
        _validate_point_in_time(value)
        ordered_points = sorted(
            value.bars,
            key=lambda item: item.bar.trade_date,
        )
        bars = [item.bar for item in ordered_points]
        if len(bars) < 60:
            raise ValueError("formal technical replay requires 60 bars")
        technical = technical_signal(bars)
        snapshot, missing = _snapshot(value)
        fundamental = fundamental_signal(snapshot)
        veto = _veto(value)
        formal = formal_result(
            DecisionShadowRequest(
                symbol=value.symbol,
                data_cutoff=value.data_cutoff,
                technical_score=technical.score,
                technical_confidence=technical.confidence,
                fundamental_score=fundamental.score,
                fundamental_confidence=fundamental.confidence,
                hard_veto=veto.veto_triggered,
                persist_shadow=False,
            )
        )
        if not value.risk_event_coverage_complete:
            missing.append("PIT_RISK_EVENT_COVERAGE_INCOMPLETE")
        if not value.historical_universe_complete:
            missing.append("HISTORICAL_UNIVERSE_INCOMPLETE")
        coverage_missing = {
            "PIT_RISK_EVENT_COVERAGE_INCOMPLETE",
            "HISTORICAL_UNIVERSE_INCOMPLETE",
        }
        if coverage_missing.intersection(missing):
            data_status = DataStatus.FAILED
        elif missing:
            data_status = DataStatus.DEGRADED
        else:
            data_status = DataStatus.FRESH
        zone_bars = [
            ZoneBar(
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        ]
        response = self.engine.decide(
            DecisionInput(
                task=TaskContext(
                    local_user_id="formal-history-read-only",
                    external_user_id="formal-history-read-only",
                    channel="research",
                    mode="DECISION",
                    symbol=value.symbol,
                    snapshot_id=(
                        f"formal-history-{value.signal_date:%Y%m%d}-"
                        f"{value.symbol}"
                    ),
                    trade_date=value.signal_date.isoformat(),
                    snapshot_time=value.data_cutoff,
                    collected_at=value.data_cutoff,
                    data_cutoff=value.data_cutoff,
                ),
                formal_score=formal.score,
                technical_score=technical.score,
                fundamental_score=fundamental.score,
                confidence=formal.confidence,
                data_status=data_status,
                veto=veto,
                current_price=bars[-1].close,
                bars=zone_bars,
                coverage_ratio=1.0 if not missing else 0.0,
                missing_data=missing,
                major_risks=[*technical.risks, *fundamental.risks],
            )
        )
        entry_zone = tuple(float(item) for item in response.frozen_entry_zone)
        preferred_zone = tuple(
            float(item) for item in response.frozen_preferred_zone
        )
        atr14 = self.engine.zones.compute_atr(zone_bars)
        point_in_time_valid = (
            data_status != DataStatus.FAILED and bool(entry_zone)
        )
        observation = HistoricalParameterObservation(
            symbol=value.symbol,
            signal_date=value.signal_date,
            score=formal.score,
            signal_close=bars[-1].close,
            atr14=atr14,
            entry_zone=(
                (entry_zone[0], entry_zone[1])
                if len(entry_zone) == 2
                else (bars[-1].close, bars[-1].close)
            ),
            future_bars=value.future_bars,
            csi300_returns=value.csi300_returns,
            csi300_returns_by_exit_date=value.csi300_returns_by_exit_date,
            point_in_time_valid=point_in_time_valid,
            score_source="FORMAL_60_40",
            market_regime=value.market_regime,
            survivorship_bias_possible=(
                not value.historical_universe_complete
            ),
        )
        action = (
            response.action.value
            if hasattr(response.action, "value")
            else str(response.action)
        )
        return FormalReplayDecision(
            symbol=value.symbol,
            signal_date=value.signal_date,
            layer=value.layer,
            market_regime=value.market_regime,
            technical_score=technical.score,
            fundamental_score=fundamental.score,
            formal_score=formal.score,
            confidence=formal.confidence,
            action=action,
            data_status=data_status.value,
            execution_status=response.execution_status,
            veto_triggered=response.veto_triggered,
            frozen_entry_zone=entry_zone,
            frozen_preferred_zone=preferred_zone,
            frozen_stop_loss_price=(
                None
                if response.frozen_stop_loss_price is None
                else float(response.frozen_stop_loss_price)
            ),
            target_position_ratio=float(response.target_position_ratio),
            recommended_batches=int(response.recommended_batches),
            point_in_time_valid=point_in_time_valid,
            missing_data=tuple(missing),
            observation=observation,
            industry_returns_by_exit_date=dict(
                value.industry_returns_by_exit_date
            ),
        )

    def replay_many(
        self,
        values: Iterable[FormalReplayInput],
    ) -> list[FormalReplayDecision]:
        return [self.replay_one(value) for value in values]


__all__ = [
    "FORMAL_HISTORY_VALIDATION_VERSION",
    "FormalReplayDecision",
    "FormalReplayInput",
    "FormalStrategyReplayService",
    "FutureDataLeakageError",
    "HistoricalBarPoint",
]