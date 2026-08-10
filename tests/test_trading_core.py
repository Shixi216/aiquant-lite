from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from trading.simulation.backtest import run_backtest
from trading.decision_support.orchestrator import TradingDecisionService
from trading.simulation.paper_account import PaperTradingEngine
from trading.simulation.portfolio import optimize_portfolio
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


def test_paper_trading_requires_approval_and_is_idempotent():
    engine = PaperTradingEngine(initial_cash=100_000)
    limits = RiskLimits(max_order_notional=50_000, require_human_approval=True)
    intent = OrderIntent(
        client_order_id="same-id",
        symbol="600000",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=10,
        price_time=datetime.now().astimezone(),
    )
    rejected = engine.submit_order(intent, limits)
    assert rejected.status == "rejected"

    approved = intent.model_copy(update={"client_order_id": "approved-id", "approved_by": "tester"})
    first = engine.submit_order(approved, limits)
    cash_after_first = engine.cash
    second = engine.submit_order(approved, limits)
    assert first.status == "filled"
    assert first.order_id == second.order_id
    assert engine.cash == cash_after_first
    assert engine.positions["600000"].quantity == 100

    oversized = approved.model_copy(
        update={"client_order_id": "oversized", "quantity": 2000}
    )
    assert (
        engine.submit_order(oversized, limits).reason
        == "Order would exceed max_position_weight"
    )


def test_paper_trading_executes_stop_loss():
    engine = PaperTradingEngine(initial_cash=100_000)
    limits = RiskLimits(max_order_notional=50_000, require_human_approval=True)
    buy = OrderIntent(
        symbol="600000",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=10,
        price_time=datetime.now().astimezone(),
        approved_by="tester",
    )
    assert engine.submit_order(buy, limits).status == "filled"
    exits = engine.execute_protective_exits({"600000": 9.0}, limits)
    assert exits[0].status == "filled"
    assert engine.positions["600000"].quantity == 0
