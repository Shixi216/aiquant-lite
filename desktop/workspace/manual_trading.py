from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from manual_tracking.trades import ManualTradeRepository, ManualTradeService
from trading.schemas import (
    ManualPosition,
    ManualTrade,
    ManualTradeConfirmation,
    ManualTradeCorrectionRequest,
    ManualTradeInput,
    ManualTradePreview,
    ManualTradeSide,
    ManualTradeSource,
)


@dataclass(frozen=True, slots=True)
class ManualTradeImpact:
    symbol: str
    position_quantity_delta: int
    cash_delta: float
    statement: str = "仅记录人工成交，不会向券商发送订单"


@dataclass(frozen=True, slots=True)
class ManualTradePreviewView:
    preview: ManualTradePreview
    impact: ManualTradeImpact
    required_confirmation: str


class ManualTradeDesktopController:
    def __init__(
        self,
        service: ManualTradeService | None = None,
        *,
        actor_id: str = "desktop-user",
        channel: str = "desktop",
    ) -> None:
        self.service = service or ManualTradeService(ManualTradeRepository())
        self.actor_id = actor_id
        self.channel = channel

    def preview(
        self,
        *,
        symbol: str,
        direction: str,
        quantity: int,
        price: float,
        trade_time: datetime,
        fee: float,
        account: str,
        note: str = "",
        source_decision_id: str | None = None,
        client_trade_id: str,
    ) -> ManualTradePreviewView:
        side = ManualTradeSide(direction.upper())
        trade = ManualTradeInput(
            portfolio_id=account,
            client_trade_id=client_trade_id,
            symbol=symbol.upper(),
            side=side,
            quantity=quantity,
            price=price,
            fees=fee,
            taxes=0,
            traded_at=trade_time,
            decision_id=source_decision_id,
            source=ManualTradeSource.USER_REPORTED,
            notes=note,
        )
        preview = self.service.create_preview(
            trade,
            requested_by=self.actor_id,
            channel=self.channel,
        )
        gross = quantity * price + fee
        sign = 1 if side == ManualTradeSide.BUY else -1
        impact = ManualTradeImpact(
            symbol=trade.symbol,
            position_quantity_delta=sign * quantity,
            cash_delta=-gross if side == ManualTradeSide.BUY else quantity * price - fee,
        )
        return ManualTradePreviewView(
            preview=preview,
            impact=impact,
            required_confirmation=(
                f"CONFIRM_MANUAL_TRADE:{preview.confirmation_id}"
            ),
        )

    def confirm(
        self,
        confirmation_id: str,
        confirmation_text: str,
    ) -> ManualTradeConfirmation:
        expected = f"CONFIRM_MANUAL_TRADE:{confirmation_id}"
        if confirmation_text != expected:
            raise ValueError("manual trade requires exact second-stage confirmation")
        return self.service.confirm_preview(
            confirmation_id,
            confirmed_by=self.actor_id,
            channel=self.channel,
        )

    def correction_preview(
        self,
        trade_id: str,
        request: ManualTradeCorrectionRequest,
    ) -> ManualTradePreview:
        return self.service.create_correction_preview(
            trade_id,
            request,
            requested_by=self.actor_id,
            channel=self.channel,
        )

    def trades(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualTrade]:
        return self.service.list_trades(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )

    def positions(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualPosition]:
        return self.service.list_positions(
            portfolio_id=portfolio_id,
            symbol=symbol,
        )


__all__ = [
    "ManualTradeDesktopController",
    "ManualTradeImpact",
    "ManualTradePreviewView",
]
