from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.task_context import TaskContext
from trading.decision_support.data_status import DataStatus
from trading.decision_support.price_zones import Bar


class NoPositionReader:
    def __init__(self) -> None:
        self.calls = 0

    def get_holding(self, local_user_id: str, symbol: str):
        self.calls += 1
        return None


class NoLivePermission:
    def can_live_trade(self, local_user_id: str) -> bool:
        return False


def test_decision_engine_accepts_read_only_position_reader() -> None:
    reader = NoPositionReader()
    engine = DecisionEngine(
        permission_service=NoLivePermission(),
        manual_positions=reader,
    )
    cutoff = datetime(2025, 1, 2, 16, tzinfo=ZoneInfo("Asia/Shanghai"))
    response = engine.decide(
        DecisionInput(
            task=TaskContext(
                local_user_id="formal-history-read-only",
                external_user_id="formal-history-read-only",
                symbol="600000.SH",
                trade_date="2025-01-02",
                snapshot_id="history-20250102-600000.SH",
                data_cutoff=cutoff,
            ),
            formal_score=0.5,
            technical_score=0.5,
            fundamental_score=0.5,
            confidence=0.8,
            data_status=DataStatus.FRESH,
            current_price=10.0,
            bars=[
                Bar(
                    trade_date=cutoff.date(),
                    open=10.0,
                    high=10.2,
                    low=9.8,
                    close=10.0,
                    volume=1000.0,
                )
            ],
        )
    )
    assert reader.calls == 1
    assert response.formal_score == 0.5