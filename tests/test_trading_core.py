from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

import pytest

from trading.backtest import run_backtest
from trading.adapter_registry import BrokerAdapterRegistry
from trading.adapters.qmt_xtquant import (
    CiticQmtXtquantPlaceholder,
    QmtPermissionNotConfirmedError,
)
from trading.broker import DisabledLiveBroker, LiveTradingDisabledError, PaperBroker
from trading.portfolio import optimize_portfolio
from trading.schemas import (
    BacktestRequest,
    Bar,
    DecisionRequest,
    FundamentalSnapshot,
    OptimizationInput,
    OptimizationRequest,
    OrderIntent,
    OrderSide,
    PortfolioState,
    RiskLimits,
)
from trading.service import TradingDecisionService


def rising_bars(count: int = 100) -> list[Bar]:
    start = date(2025, 1, 1)
    bars: list[Bar] = []
    for index in range(count):
        close = 10 + index * 0.05
        bars.append(
            Bar(
                trade_date=start + timedelta(days=index),
                open=close - 0.02,
                high=close + 0.10,
                low=close - 0.10,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


@pytest.mark.asyncio
async def test_parallel_decision_produces_structured_trace_without_chain_of_thought():
    trace = await TradingDecisionService().decide(
        DecisionRequest(
            symbol="600000",
            bars=rising_bars(),
            fundamentals=FundamentalSnapshot(
                as_of=date(2025, 3, 31),
                roe=0.18,
                revenue_growth=0.20,
                debt_ratio=0.40,
                operating_cash_flow_positive=True,
                evidence_refs=["statement:2025Q1"],
            ),
            portfolio=PortfolioState(cash=900_000, equity=1_000_000),
        )
    )

    assert {opinion.role for opinion in trace.opinions} == {
        "technical_agent",
        "fundamental_agent",
        "strategy_agent",
    }
    assert trace.final_action == "buy"
    assert "not hidden chain-of-thought" in trace.audit_notice
    assert "prompt" not in trace.model_dump()


@pytest.mark.asyncio
async def test_risk_agent_has_deterministic_veto_authority():
    trace = await TradingDecisionService().decide(
        DecisionRequest(
            symbol="600000",
            bars=rising_bars(),
            fundamentals=FundamentalSnapshot(
                as_of=date(2025, 3, 31),
                roe=0.18,
                revenue_growth=0.20,
                debt_ratio=0.40,
                operating_cash_flow_positive=True,
            ),
            portfolio=PortfolioState(
                cash=900_000,
                equity=1_000_000,
                kill_switch=True,
            ),
        )
    )

    assert trace.final_action == "veto"
    assert trace.risk_verdict.vetoed is True
    assert "Emergency kill switch is active" in trace.risk_verdict.reasons


def test_portfolio_optimizer_respects_exposure_and_per_asset_caps():
    result = optimize_portfolio(
        OptimizationRequest(
            assets=[
                OptimizationInput(symbol="600000", expected_score=0.8, volatility=0.2),
                OptimizationInput(symbol="000001", expected_score=0.4, volatility=0.3),
                OptimizationInput(symbol="300001", expected_score=-0.5, volatility=0.4),
            ],
            max_position_weight=0.30,
            max_gross_exposure=0.50,
        )
    )

    assert sum(result.weights.values()) <= 0.50000001
    assert max(result.weights.values()) <= 0.30
    assert result.weights["300001"] == 0
    assert result.cash_weight >= 0.50


def test_backtest_uses_next_open_and_reports_costs_and_drawdown():
    result = run_backtest(BacktestRequest(symbol="600000", bars=rising_bars(120)))

    assert result.trades
    assert result.trades[0].side == "buy"
    assert result.trades[0].trade_date == rising_bars(120)[60].trade_date
    assert result.trades[0].fee > 0
    assert result.max_drawdown <= 0
    assert "previous close" in result.assumptions[0]


def test_paper_broker_requires_approval_and_is_idempotent():
    broker = PaperBroker(initial_cash=100_000)
    limits = RiskLimits(max_order_notional=50_000, require_human_approval=True)
    intent = OrderIntent(
        client_order_id="same-id",
        symbol="600000",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=10,
        price_time=datetime.now().astimezone(),
    )
    rejected = broker.submit_order(intent, limits)
    assert rejected.status == "rejected"

    approved = intent.model_copy(update={"client_order_id": "approved-id", "approved_by": "tester"})
    first = broker.submit_order(approved, limits)
    cash_after_first = broker.cash
    second = broker.submit_order(approved, limits)
    assert first.status == "filled"
    assert first.order_id == second.order_id
    assert broker.cash == cash_after_first
    assert broker.positions["600000"].quantity == 100

    oversized = approved.model_copy(
        update={"client_order_id": "oversized", "quantity": 2000}
    )
    assert broker.submit_order(oversized, limits).reason == "Order would exceed max_position_weight"


def test_paper_broker_executes_stop_loss_and_live_broker_fails_closed():
    broker = PaperBroker(initial_cash=100_000)
    limits = RiskLimits(max_order_notional=50_000, require_human_approval=True)
    buy = OrderIntent(
        symbol="600000",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=10,
        price_time=datetime.now().astimezone(),
        approved_by="tester",
    )
    assert broker.submit_order(buy, limits).status == "filled"
    exits = broker.execute_protective_exits({"600000": 9.0}, limits)
    assert exits[0].status == "filled"
    assert broker.positions["600000"].quantity == 0

    with pytest.raises(LiveTradingDisabledError, match="Live trading is disabled"):
        DisabledLiveBroker().submit_order(buy, limits)


def test_citic_qmt_placeholder_never_imports_or_executes_xtquant():
    adapter = CiticQmtXtquantPlaceholder()
    capabilities = adapter.capabilities()

    assert "xtquant" not in sys.modules
    assert capabilities.broker_name == "中信证券"
    assert capabilities.mode == "live_disabled"
    assert capabilities.execution_enabled is False
    assert capabilities.environment_probed is False
    assert capabilities.credentials_stored is False
    assert "submit_order" in capabilities.planned_operations

    intent = OrderIntent(
        symbol="600000",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=10,
        price_time=datetime.now().astimezone(),
        approved_by="tester",
    )
    with pytest.raises(QmtPermissionNotConfirmedError, match="Paper trading remains active"):
        adapter.submit_order(intent, RiskLimits())
    with pytest.raises(QmtPermissionNotConfirmedError):
        adapter.account_snapshot()
    with pytest.raises(QmtPermissionNotConfirmedError):
        adapter.list_orders()
    with pytest.raises(QmtPermissionNotConfirmedError):
        adapter.list_trades()
    with pytest.raises(QmtPermissionNotConfirmedError):
        adapter.cancel_order("never-sent")


def test_broker_registry_keeps_paper_as_only_active_adapter():
    registry = BrokerAdapterRegistry(active_adapter_id="paper")
    registry.register(PaperBroker())
    registry.register(CiticQmtXtquantPlaceholder())
    catalog = registry.catalog()

    assert catalog.active_adapter_id == "paper"
    assert [item.adapter_id for item in catalog.adapters] == ["citic_qmt_xtquant", "paper"]
    qmt = catalog.adapters[0].model_dump()
    forbidden_keys = {"account", "account_id", "password", "trading_password", "cookie", "secret", "key"}
    assert forbidden_keys.isdisjoint(qmt)
