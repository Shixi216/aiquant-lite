from __future__ import annotations

from trading.broker import BrokerAdapter, LiveTradingDisabledError
from trading.schemas import (
    BrokerCapabilities,
    BrokerAccountSnapshot,
    BrokerMode,
    CancelOrderResult,
    OrderIntent,
    OrderResult,
    RiskLimits,
)


class QmtPermissionNotConfirmedError(LiveTradingDisabledError):
    pass


class CiticQmtXtquantPlaceholder(BrokerAdapter):
    """Non-connecting contract placeholder for a possible future CITIC QMT adapter.

    This class deliberately does not import xtquant, inspect QMT installation paths,
    accept account identifiers, or store authentication material.
    """

    @property
    def adapter_id(self) -> str:
        return "citic_qmt_xtquant"

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            adapter_id=self.adapter_id,
            broker_name="中信证券",
            mode=BrokerMode.LIVE_DISABLED,
            status="awaiting_permission_confirmation",
            available=False,
            execution_enabled=False,
            environment_probed=False,
            credentials_stored=False,
            supported_operations=[],
            planned_operations=[
                "connect_and_subscribe",
                "account_snapshot",
                "query_positions",
                "query_orders",
                "query_trades",
                "submit_order",
                "cancel_order",
                "order_and_trade_callbacks",
                "reconnect_and_reconcile",
            ],
            blockers=[
                "QMT entitlement has not been confirmed with CITIC Securities",
                "Python API entitlement has not been confirmed",
                "Paper/simulation environment has not been confirmed",
                "Official broker onboarding and sandbox acceptance are incomplete",
            ],
            security_guards=[
                "No xtquant import or runtime discovery",
                "No account, password, trading password, Cookie, token, or key fields",
                "Every operation raises a fail-closed exception",
                "Paper adapter remains the only active execution adapter",
            ],
        )

    @staticmethod
    def _raise_unavailable() -> None:
        raise QmtPermissionNotConfirmedError(
            "CITIC QMT/xtquant is reserved but disabled: account entitlements and a simulation "
            "environment have not been confirmed. Paper trading remains active."
        )

    def account_snapshot(self) -> BrokerAccountSnapshot:
        self._raise_unavailable()

    def list_orders(self) -> list[OrderResult]:
        self._raise_unavailable()

    def list_trades(self) -> list[OrderResult]:
        self._raise_unavailable()

    def cancel_order(self, client_order_id: str) -> CancelOrderResult:
        self._raise_unavailable()

    def submit_order(self, intent: OrderIntent, limits: RiskLimits) -> OrderResult:
        self._raise_unavailable()
