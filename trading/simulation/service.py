from __future__ import annotations

from trading.simulation.backtest import run_backtest
from trading.simulation.paper_account import PaperTradingEngine
from trading.simulation.persistence import TradingAuditStore
from trading.simulation.portfolio import optimize_portfolio
from trading.schemas import (
    BacktestRequest,
    BacktestResult,
    OptimizationRequest,
    OptimizationResult,
    OrderIntent,
    OrderResult,
    PaperAccount,
    RiskLimits,
)


class SimulationService:
    """Own deterministic backtests, optimization, and local Paper Trading."""

    def __init__(
        self,
        *,
        audit_store: TradingAuditStore | None = None,
        paper_trading: PaperTradingEngine | None = None,
    ) -> None:
        self.audit_store = audit_store or TradingAuditStore()
        self.paper_trading = paper_trading or PaperTradingEngine()
        self._paper_state_loaded = False

    def _ensure_paper_state(self) -> None:
        if self._paper_state_loaded:
            return
        account = self.audit_store.load_paper_account()
        if account is not None:
            self.paper_trading.restore(account)
        self._paper_state_loaded = True

    def backtest(self, request: BacktestRequest) -> BacktestResult:
        return run_backtest(request)

    def optimize_portfolio(self, request: OptimizationRequest) -> OptimizationResult:
        return optimize_portfolio(request)

    def submit_paper_order(
        self,
        intent: OrderIntent,
        risk_limits: RiskLimits,
    ) -> OrderResult:
        self._ensure_paper_state()
        result = self.paper_trading.submit_order(intent, risk_limits)
        self.audit_store.record_order(result)
        self.audit_store.record_paper_account(self.paper_trading.account())
        return result

    def execute_protective_exits(
        self,
        prices: dict[str, float],
        risk_limits: RiskLimits,
    ) -> list[OrderResult]:
        self._ensure_paper_state()
        results = self.paper_trading.execute_protective_exits(prices, risk_limits)
        for result in results:
            self.audit_store.record_order(result)
        self.audit_store.record_paper_account(self.paper_trading.account())
        return results

    def paper_account(self) -> PaperAccount:
        self._ensure_paper_state()
        return self.paper_trading.account()

    def paper_orders(self) -> list[OrderResult]:
        return self.audit_store.list_orders("paper")

    def set_kill_switch(self, active: bool) -> PaperAccount:
        self._ensure_paper_state()
        self.paper_trading.kill_switch = active
        account = self.paper_trading.account()
        self.audit_store.record_paper_account(account)
        return account
