from __future__ import annotations

from datetime import datetime, timedelta

from trading.decision_support.decision_packets import DecisionRepository
from manual_tracking.trades.repository import ManualTradeRepository
from trading.schemas import (
    ManualLedgerEventType,
    ManualPosition,
    ManualTrade,
    ManualTradeConfirmation,
    ManualTradeCorrectionRequest,
    ManualTradeInput,
    ManualTradePreview,
    ManualTradePreviewPayload,
    ManualTradeSide,
    ManualTradeSource,
)


def _now() -> datetime:
    return datetime.now().astimezone()


class ManualTradeService:
    """Record only user-confirmed external trade facts through previews."""

    def __init__(
        self,
        repository: ManualTradeRepository,
        decision_repository: DecisionRepository | None = None,
    ) -> None:
        self.repository = repository
        self.decision_repository = decision_repository

    def _validate_decision_reference(self, decision_id: str | None) -> None:
        if decision_id is None or self.decision_repository is None:
            return
        self.decision_repository.get_latest(decision_id)

    def create_preview(
        self,
        trade: ManualTradeInput,
        *,
        requested_by: str,
        channel: str,
        expires_in_seconds: int = 600,
    ) -> ManualTradePreview:
        self._validate_decision_reference(trade.decision_id)
        created_at = _now()
        payload = ManualTradePreviewPayload(
            event_type=ManualLedgerEventType.TRADE,
            trade=trade,
        )
        return self.repository.create_preview(
            payload=payload,
            requested_by=requested_by,
            channel=channel,
            expires_at=created_at + timedelta(seconds=expires_in_seconds),
            created_at=created_at,
        )

    def create_correction_preview(
        self,
        trade_id: str,
        request: ManualTradeCorrectionRequest,
        *,
        requested_by: str,
        channel: str,
    ) -> ManualTradePreview:
        original = self.repository.get_trade(trade_id)
        created_at = _now()
        event_type = ManualLedgerEventType(request.correction_type)
        if event_type == ManualLedgerEventType.CORRECTION:
            replacement = request.replacement
            if replacement is None:
                raise ValueError("CORRECTION requires a replacement trade")
            trade = ManualTradeInput(
                portfolio_id=original.portfolio_id,
                client_trade_id=replacement.client_trade_id,
                symbol=original.symbol,
                side=replacement.side,
                quantity=replacement.quantity,
                price=replacement.price,
                fees=replacement.fees,
                taxes=replacement.taxes,
                traded_at=replacement.traded_at,
                decision_id=original.decision_id,
                source=ManualTradeSource.USER_REPORTED,
                notes=replacement.notes,
            )
        else:
            if request.client_trade_id is None:
                raise ValueError("REVERSAL requires client_trade_id")
            trade = ManualTradeInput(
                portfolio_id=original.portfolio_id,
                client_trade_id=request.client_trade_id,
                symbol=original.symbol,
                side=(
                    ManualTradeSide.SELL
                    if original.side == ManualTradeSide.BUY
                    else ManualTradeSide.BUY
                ),
                quantity=original.quantity,
                price=original.price,
                fees=original.fees,
                taxes=original.taxes,
                traded_at=original.traded_at,
                decision_id=original.decision_id,
                source=ManualTradeSource.USER_REPORTED,
                notes=f"REVERSAL: {request.reason}",
            )
        payload = ManualTradePreviewPayload(
            event_type=event_type,
            trade=trade,
            correction_of_trade_id=original.trade_id,
            correction_reason=request.reason,
        )
        return self.repository.create_preview(
            payload=payload,
            requested_by=requested_by,
            channel=channel,
            expires_at=created_at + timedelta(seconds=request.expires_in_seconds),
            created_at=created_at,
        )

    def get_preview(self, confirmation_id: str) -> ManualTradePreview:
        return self.repository.get_preview(confirmation_id, now=_now())

    def confirm_preview(
        self,
        confirmation_id: str,
        *,
        confirmed_by: str,
        channel: str,
    ) -> ManualTradeConfirmation:
        return self.repository.confirm_preview(
            confirmation_id,
            actor_id=confirmed_by,
            channel=channel,
            now=_now(),
        )

    def cancel_preview(
        self,
        confirmation_id: str,
        *,
        cancelled_by: str,
        channel: str,
    ) -> ManualTradePreview:
        return self.repository.cancel_preview(
            confirmation_id,
            actor_id=cancelled_by,
            channel=channel,
            now=_now(),
        )

    def list_trades(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualTrade]:
        return self.repository.list_trades(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )

    def get_trade(self, trade_id: str) -> ManualTrade:
        return self.repository.get_trade(trade_id)

    def list_positions(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualPosition]:
        return self.repository.list_positions(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )

    def get_position(self, position_id: str) -> ManualPosition:
        return self.repository.get_position(position_id)
