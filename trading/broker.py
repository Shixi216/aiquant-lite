from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from trading.risk import validate_order_clock
from trading.schemas import (
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    PaperAccount,
    Position,
    RiskLimits,
)


class BrokerAdapter(ABC):
    @abstractmethod
    def submit_order(self, intent: OrderIntent, limits: RiskLimits) -> OrderResult: ...


class LiveTradingDisabledError(RuntimeError):
    pass


class DisabledLiveBroker(BrokerAdapter):
    """Permanent fail-closed placeholder until a named broker passes certification."""

    def submit_order(self, intent: OrderIntent, limits: RiskLimits) -> OrderResult:
        raise LiveTradingDisabledError(
            "Live trading is disabled: no certified broker adapter, sandbox acceptance, "
            "reconciliation, two-person approval, or production kill switch is configured."
        )


class PaperBroker(BrokerAdapter):
    def __init__(self, initial_cash: float = 1_000_000, commission_rate: float = 0.0003) -> None:
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.kill_switch = False
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, OrderResult] = {}

    def restore(self, account: PaperAccount) -> None:
        self.cash = account.cash
        self.positions = dict(account.positions)
        self.kill_switch = account.kill_switch

    def account(self) -> PaperAccount:
        market_value = sum(position.quantity * position.last_price for position in self.positions.values())
        return PaperAccount(
            cash=self.cash,
            positions=self.positions,
            equity=self.cash + market_value,
            kill_switch=self.kill_switch,
        )

    @staticmethod
    def _rejected(intent: OrderIntent, reason: str) -> OrderResult:
        return OrderResult(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.REJECTED,
            reason=reason,
        )

    def submit_order(self, intent: OrderIntent, limits: RiskLimits) -> OrderResult:
        existing = self.orders.get(intent.client_order_id)
        if existing is not None:
            return existing
        rejection: str | None = None
        if self.kill_switch and intent.side == OrderSide.BUY:
            rejection = "Paper-trading kill switch is active"
        elif clock_error := validate_order_clock(intent.price_time, limits.max_price_age_seconds):
            rejection = clock_error
        elif intent.side == OrderSide.BUY and intent.quantity % 100:
            rejection = "A-share buy quantity must be a multiple of 100"
        elif intent.reference_price * intent.quantity > limits.max_order_notional:
            rejection = "Order notional exceeds max_order_notional"
        elif limits.require_human_approval and not intent.approved_by:
            rejection = "Human approval identity is required"
        position = self.positions.get(
            intent.symbol,
            Position(symbol=intent.symbol, quantity=0, average_cost=0, last_price=intent.reference_price),
        )
        if not rejection and intent.side == OrderSide.SELL and intent.quantity > position.quantity:
            rejection = "Sell quantity exceeds paper position"
        fee = intent.reference_price * intent.quantity * self.commission_rate
        if not rejection and intent.side == OrderSide.BUY:
            required_cash = intent.reference_price * intent.quantity + fee
            if required_cash > self.cash:
                rejection = "Insufficient paper cash"
            else:
                account = self.account()
                current_market_value = account.equity - account.cash
                projected_equity = account.equity - fee
                projected_position_value = (
                    position.quantity * intent.reference_price
                    + intent.quantity * intent.reference_price
                )
                projected_exposure = current_market_value + intent.quantity * intent.reference_price
                projected_cash = self.cash - required_cash
                if projected_position_value / projected_equity > limits.max_position_weight:
                    rejection = "Order would exceed max_position_weight"
                elif projected_exposure / projected_equity > limits.max_gross_exposure:
                    rejection = "Order would exceed max_gross_exposure"
                elif projected_cash / projected_equity < limits.min_cash_weight:
                    rejection = "Order would breach min_cash_weight"
        if rejection:
            result = self._rejected(intent, rejection)
            self.orders[intent.client_order_id] = result
            return result

        gross = intent.reference_price * intent.quantity
        if intent.side == OrderSide.BUY:
            old_cost = position.average_cost * position.quantity
            new_quantity = position.quantity + intent.quantity
            position = position.model_copy(update={
                "quantity": new_quantity,
                "average_cost": (old_cost + gross + fee) / new_quantity,
                "last_price": intent.reference_price,
            })
            self.cash -= gross + fee
        else:
            realized = (intent.reference_price - position.average_cost) * intent.quantity - fee
            position = position.model_copy(update={
                "quantity": position.quantity - intent.quantity,
                "last_price": intent.reference_price,
                "realized_pnl": position.realized_pnl + realized,
            })
            self.cash += gross - fee
        self.positions[intent.symbol] = position
        result = OrderResult(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.FILLED,
            fill_price=intent.reference_price,
            fee=fee,
        )
        self.orders[intent.client_order_id] = result
        return result

    def execute_protective_exits(
        self,
        prices: dict[str, float],
        limits: RiskLimits,
    ) -> list[OrderResult]:
        results: list[OrderResult] = []
        for symbol, position in list(self.positions.items()):
            price = prices.get(symbol)
            if not price or position.quantity <= 0:
                continue
            position = position.model_copy(update={"last_price": price})
            self.positions[symbol] = position
            loss_hit = price <= position.average_cost * (1 - limits.stop_loss_pct)
            profit_hit = price >= position.average_cost * (1 + limits.take_profit_pct)
            if not (loss_hit or profit_hit):
                continue
            reason = "stop-loss" if loss_hit else "take-profit"
            intent = OrderIntent(
                client_order_id=f"protective-{symbol}-{reason}-{datetime.now().date().isoformat()}",
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=position.quantity,
                reference_price=price,
                price_time=datetime.now().astimezone(),
                approved_by="deterministic-protective-rule",
            )
            results.append(self.submit_order(intent, limits))
        return results
