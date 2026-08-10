from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from data_hub.schemas.unified import VerificationStatus
from trading.experiments.formal_strategy_validation import (
    FormalReplayInput,
    FormalStrategyReplayService,
    FutureDataLeakageError,
    HistoricalBarPoint,
)
from trading.experiments.parameter_sensitivity import FutureBar
from trading.research.fundamental.models import (
    MarketValuationPoint,
    PointInTimeFinancialRecord,
)
from trading.scanner.production_partition import CandidateLayer
from trading.schemas import Bar


TZ = ZoneInfo("Asia/Shanghai")
SIGNAL_DATE = date(2025, 4, 30)
CUTOFF = datetime(2025, 4, 30, 16, tzinfo=TZ)


def bars() -> tuple[HistoricalBarPoint, ...]:
    output = []
    start = SIGNAL_DATE - timedelta(days=89)
    for index in range(60):
        trade_date = start + timedelta(days=index)
        price = 10.0 + index * 0.03
        output.append(
            HistoricalBarPoint(
                bar=Bar(
                    trade_date=trade_date,
                    open=price,
                    high=price + 0.2,
                    low=price - 0.2,
                    close=price + 0.1,
                    volume=1000 + index,
                ),
                data_available_time=datetime.combine(
                    trade_date,
                    datetime.min.time(),
                    tzinfo=TZ,
                ).replace(hour=16),
                data_cutoff=datetime.combine(
                    trade_date,
                    datetime.min.time(),
                    tzinfo=TZ,
                ).replace(hour=16),
            )
        )
    return tuple(output)


def record(
    statement: str,
    period: str,
    available: datetime,
    payload: dict,
) -> PointInTimeFinancialRecord:
    return PointInTimeFinancialRecord(
        canonical_record_id=f"{statement}-{period}",
        symbol="600000.SH",
        data_type=f"financial_statement:{statement}",
        report_period=period,
        announcement_time=available,
        data_available_time=available,
        event_time=available,
        primary_source="test PIT",
        source_record_ids=[f"raw-{statement}-{period}"],
        verification_status=VerificationStatus.VERIFIED,
        confidence=1.0,
        statement_type=statement,
        accounting_scope="1",
        period_type="ANNUAL",
        source_type="CANONICAL",
        disclosure_time_source="ann_date",
        payload=payload,
    )


def financials() -> tuple[PointInTimeFinancialRecord, ...]:
    available = datetime(2025, 3, 31, 18, tzinfo=TZ)
    return (
        record("balancesheet", "20241231", available, {
            "total_liab": 40.0, "total_assets": 100.0,
            "total_hldr_eqy_exc_min_int": 60.0, "total_share": 10.0,
        }),
        record("balancesheet", "20231231", datetime(2024, 3, 31, 18, tzinfo=TZ), {
            "total_liab": 45.0, "total_assets": 100.0,
            "total_hldr_eqy_exc_min_int": 55.0, "total_share": 10.0,
        }),
        record("income", "20241231", available, {
            "revenue": 120.0, "n_income_attr_p": 12.0,
        }),
        record("income", "20231231", datetime(2024, 3, 31, 18, tzinfo=TZ), {
            "revenue": 100.0, "n_income_attr_p": 10.0,
        }),
        record("cashflow", "20241231", available, {
            "n_cashflow_act": 15.0,
        }),
    )


def replay_input(**updates) -> FormalReplayInput:
    payload = dict(
        symbol="600000.SH",
        signal_date=SIGNAL_DATE,
        data_cutoff=CUTOFF,
        layer=CandidateLayer.CORE,
        bars=bars(),
        financial_records=financials(),
        valuation_point=MarketValuationPoint(
            canonical_record_id="valuation",
            source_record_ids=["bar"],
            event_time=CUTOFF,
            close=11.8,
        ),
        future_bars=tuple(
            FutureBar(
                trade_date=SIGNAL_DATE + timedelta(days=index + 1),
                open=11.7,
                high=12.0,
                low=11.5,
                close=11.8,
                previous_close=11.7,
                volume=1000,
            )
            for index in range(20)
        ),
        risk_event_coverage_complete=True,
        historical_universe_complete=True,
    )
    payload.update(updates)
    return FormalReplayInput(**payload)


def test_replay_uses_formal_60_40_and_frozen_zones() -> None:
    result = FormalStrategyReplayService().replay_one(replay_input())
    assert result.formal_score == pytest.approx(
        result.technical_score * 0.60 + result.fundamental_score * 0.40
    )
    assert result.observation.score_source == "FORMAL_60_40"
    assert result.frozen_entry_zone
    assert result.frozen_preferred_zone
    assert not result.missing_data


def test_future_financial_record_is_rejected() -> None:
    future = list(financials())
    future.append(
        record(
            "income",
            "20250331",
            CUTOFF + timedelta(days=1),
            {"revenue": 130.0, "n_income_attr_p": 13.0},
        )
    )
    with pytest.raises(FutureDataLeakageError):
        FormalStrategyReplayService().replay_one(
            replay_input(financial_records=tuple(future))
        )


def test_incomplete_risk_event_coverage_blocks_formal_sample() -> None:
    result = FormalStrategyReplayService().replay_one(
        replay_input(risk_event_coverage_complete=False)
    )
    assert not result.point_in_time_valid
    assert "PIT_RISK_EVENT_COVERAGE_INCOMPLETE" in result.missing_data

def test_partial_pit_fundamentals_are_degraded_not_failed() -> None:
    partial = tuple(
        item for item in financials() if item.statement_type != "cashflow"
    )
    result = FormalStrategyReplayService().replay_one(
        replay_input(financial_records=partial)
    )
    assert result.data_status == "DEGRADED"
    assert "operating_cash_flow" in result.missing_data


def test_formal_replay_trade_plan_boundary_is_explicitly_read_only() -> None:
    service = FormalStrategyReplayService()
    with pytest.raises(RuntimeError, match="read-only"):
        service.engine.plan_repo.create_plan(None)