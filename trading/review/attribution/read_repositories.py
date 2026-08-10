from __future__ import annotations

from datetime import date
from trading.decision_support.decision_packets import DecisionRepository
from manual_tracking.risk_reviews import ManualPositionRiskReviewRepository
from manual_tracking.trades import ManualTradeRepository
from trading.simulation.persistence import TradingAuditStore
from trading.schemas import (
    DailyReview,
    DecisionPacket,
    ManualPosition,
    ManualPositionRiskReview,
    ManualTrade,
    PaperAccount,
)


class DecisionReadRepository:
    """A capability-limited, read-only view of DecisionRepository."""

    __slots__ = ("_source",)

    def __init__(self, source: DecisionRepository) -> None:
        self._source = source

    def get(self, decision_id: str, decision_version: int) -> DecisionPacket:
        return self._source.get(decision_id, decision_version)

    def get_latest(self, decision_id: str) -> DecisionPacket:
        return self._source.get_latest(decision_id)

    def list_versions(self, decision_id: str) -> list[DecisionPacket]:
        return self._source.list_versions(decision_id)


class SimulationReadRepository:
    """Read-only simulation and audit projections used by daily reviews."""

    __slots__ = ("_source",)

    def __init__(self, source: TradingAuditStore) -> None:
        self._source = source

    def daily_review(self, target_date: date) -> DailyReview:
        return self._source.daily_review(target_date)

    def load_paper_account(self) -> PaperAccount | None:
        return self._source.load_paper_account()


class ManualTradeReadRepository:
    """Read-only manual-trade facts and the derived position projection."""

    __slots__ = ("_source",)

    def __init__(self, source: ManualTradeRepository) -> None:
        self._source = source

    def get_trade(self, trade_id: str) -> ManualTrade:
        return self._source.get_trade(trade_id)

    def list_trades(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualTrade]:
        return self._source.list_trades(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )

    def get_position(self, position_id: str) -> ManualPosition:
        return self._source.get_position(position_id)

    def list_positions(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualPosition]:
        return self._source.list_positions(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )


class ManualPositionRiskReviewReadRepository:
    """Read-only view of append-only manual-position risk reviews."""

    __slots__ = ("_source",)

    def __init__(self, source: ManualPositionRiskReviewRepository) -> None:
        self._source = source

    def list_for_position(
        self,
        position_id: str,
    ) -> list[ManualPositionRiskReview]:
        return self._source.list_for_position(position_id)

    def latest_for_position(self, position_id: str) -> ManualPositionRiskReview:
        return self._source.latest_for_position(position_id)

    def list_for_date(self, target_date: date) -> list[ManualPositionRiskReview]:
        return self._source.list_for_date(target_date)
