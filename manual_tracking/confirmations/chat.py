from __future__ import annotations

import re
from dataclasses import dataclass

from manual_tracking.trades import ManualTradeService
from trading.schemas import (
    ManualTradeConfirmation,
    ManualTradeInput,
    ManualTradePreview,
)


CONFIRMATION_PATTERN = re.compile(r"^确认录入\s+([0-9a-f]{32})$")


@dataclass(frozen=True)
class ManualTradeChatReply:
    text: str
    preview: ManualTradePreview | None = None
    confirmation: ManualTradeConfirmation | None = None


class ManualTradeChatService:
    """Fail-closed contract for an authenticated Hermes chat connector."""

    def __init__(self, manual_trade_service: ManualTradeService) -> None:
        self.manual_trade_service = manual_trade_service

    def preview_natural_language_trade(
        self,
        *,
        message: str,
        normalized_trade: ManualTradeInput,
        authenticated_user: str,
        channel: str,
    ) -> ManualTradeChatReply:
        if not message.strip():
            raise ValueError("chat message must not be empty")
        preview = self.manual_trade_service.create_preview(
            normalized_trade,
            requested_by=authenticated_user,
            channel=channel,
        )
        return ManualTradeChatReply(
            text=(
                "仅生成待确认的人工成交预览，尚未写入人工成交账本。"
                f"如内容无误，请回复：确认录入 {preview.confirmation_id}"
            ),
            preview=preview,
        )

    def confirm_explicit_message(
        self,
        *,
        message: str,
        authenticated_user: str,
        channel: str,
    ) -> ManualTradeChatReply:
        match = CONFIRMATION_PATTERN.fullmatch(message.strip())
        if match is None:
            raise ValueError(
                "manual trade confirmation must exactly match: "
                "确认录入 <confirmation_id>"
            )
        confirmation = self.manual_trade_service.confirm_preview(
            match.group(1),
            confirmed_by=authenticated_user,
            channel=channel,
        )
        return ManualTradeChatReply(
            text=f"人工成交已确认录入：{confirmation.trade.trade_id}",
            confirmation=confirmation,
        )
