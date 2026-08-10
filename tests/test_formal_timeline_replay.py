from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.experiments.formal_strategy_validation import (
    FormalReplayDecision,
    FormalReplayInput,
    HistoricalBarPoint,
)
from trading.experiments.formal_timeline_replay import FormalTimelineReplayService
from trading.experiments.parameter_sensitivity import FutureBar, HistoricalParameterObservation
from trading.scanner.production_partition import CandidateLayer
from trading.schemas import Bar


TZ = ZoneInfo("Asia/Shanghai")


def _input(symbol: str, signal_date: date, future: tuple[FutureBar, ...]):
    history = tuple(
        HistoricalBarPoint(
            bar=Bar(
                trade_date=signal_date - timedelta(days=80-index),
                open=9+index/100, high=9.2+index/100,
                low=8.8+index/100, close=9.1+index/100,
                volume=1000,
            ),
            data_available_time=datetime.combine(
                signal_date - timedelta(days=80-index),
                datetime.min.time(), tzinfo=TZ,
            ).replace(hour=16),
            data_cutoff=datetime.combine(
                signal_date - timedelta(days=80-index),
                datetime.min.time(), tzinfo=TZ,
            ).replace(hour=16),
        )
        for index in range(60)
    )
    return FormalReplayInput(
        symbol=symbol, signal_date=signal_date,
        data_cutoff=datetime.combine(signal_date, datetime.min.time(), tzinfo=TZ).replace(hour=16),
        layer=CandidateLayer.CORE, bars=history, financial_records=(),
        valuation_point=None, future_bars=future,
        risk_event_coverage_complete=True,
        historical_universe_complete=True,
    )


def _decision(symbol: str, signal_date: date, future: tuple[FutureBar, ...], action="BUY"):
    observation = HistoricalParameterObservation(
        symbol=symbol, signal_date=signal_date, score=.6,
        signal_close=10, atr14=.5, entry_zone=(9.4,10.6),
        future_bars=future, point_in_time_valid=True,
    )
    return FormalReplayDecision(
        symbol=symbol, signal_date=signal_date, layer=CandidateLayer.CORE,
        market_regime="SIDEWAYS", technical_score=.7, fundamental_score=.2,
        formal_score=.5, confidence=.7, action=action, data_status="FRESH",
        execution_status="等待回调", veto_triggered=False,
        frozen_entry_zone=(9.4,10.6), frozen_preferred_zone=(9.4,9.9),
        frozen_stop_loss_price=8.8, target_position_ratio=.12,
        recommended_batches=2, point_in_time_valid=True, missing_data=(),
        observation=observation, industry_returns_by_exit_date={},
    )


def _future(signal_date: date, closes: list[float]):
    return tuple(
        FutureBar(
            trade_date=signal_date + timedelta(days=index+1),
            open=value, high=value+.2, low=value-.2, close=value,
            previous_close=(10 if index==0 else closes[index-1]),
            volume=1000,
        )
        for index,value in enumerate(closes)
    )


def test_frozen_zones_are_reused_until_preferred_entry():
    signal=date(2025,1,2); future=_future(signal,[10.2,9.8,10.1,10.2,10.3])
    decision=_decision("000001.SZ",signal,future)
    result=FormalTimelineReplayService().replay([decision],[_input("000001.SZ",signal,future)])
    outcome=result.outcomes[0]
    assert outcome.entered_preferred_zone
    assert outcome.preferred_entry_day==2
    assert outcome.packet.frozen_entry_zone==(9.4,10.6)
    assert outcome.packet.frozen_preferred_zone==(9.4,9.9)
    assert outcome.returns[1] == pytest.approx(10.1 / 9.8 - 1)
    with pytest.raises(Exception):
        outcome.packet.frozen_entry_zone[0]=0


def test_same_direction_active_packet_suppresses_daily_duplicate():
    first=date(2025,1,2); second=date(2025,1,3)
    future1=_future(first,[10.2,10.1,10.0,10.0,10.0,10.0])
    future2=_future(second,[10.1,10.0,10.0,10.0,10.0])
    decisions=[_decision("000001.SZ",first,future1),_decision("000001.SZ",second,future2)]
    inputs=[_input("000001.SZ",first,future1),_input("000001.SZ",second,future2)]
    result=FormalTimelineReplayService().replay(decisions,inputs)
    assert len(result.packets)==1
    assert result.duplicate_signals_skipped==1


def test_forbid_chase_and_expiry_are_terminal_without_order_creation():
    signal=date(2025,1,2)
    chase=_future(signal,[11.0,11.1,11.2])
    wait=_future(signal,[10.2,10.2,10.2,10.2,10.2,10.2])
    decisions=[_decision("000001.SZ",signal,chase),_decision("000002.SZ",signal,wait)]
    inputs=[_input("000001.SZ",signal,chase),_input("000002.SZ",signal,wait)]
    result=FormalTimelineReplayService().replay(decisions,inputs)
    reasons={item.packet.symbol:item.terminal_reason for item in result.outcomes}
    assert reasons["000001.SZ"]=="FORBID_CHASE"
    assert reasons["000002.SZ"]=="EXPIRED"
    assert result.orders_created==0
    assert result.positions_changed==0