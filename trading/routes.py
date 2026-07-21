from __future__ import annotations

import asyncio
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field

from data_hub.services.daily_bars_service import DailyBarsService
from trading.adapter_registry import BrokerAdapterRegistry
from trading.adapters import CiticQmtXtquantPlaceholder
from trading.backtest import run_backtest
from trading.broker import LiveTradingDisabledError, PaperBroker
from trading.persistence import TradingAuditStore
from trading.portfolio import optimize_portfolio
from trading.replay import render_daily_review_markdown
from trading.schemas import (
    BacktestRequest,
    BacktestResult,
    BrokerCatalog,
    DailyReview,
    DecisionRequest,
    DecisionFromDataRequest,
    DecisionTrace,
    OptimizationRequest,
    OptimizationResult,
    OrderIntent,
    OrderResult,
    PaperAccount,
    RiskLimits,
    TradingModel,
)
from trading.service import TradingDecisionService


router = APIRouter(prefix="/v1/trading", tags=["trading"])
decision_service = TradingDecisionService()
audit_store = TradingAuditStore()
paper_broker = PaperBroker()
live_broker = CiticQmtXtquantPlaceholder()
broker_registry = BrokerAdapterRegistry(active_adapter_id=paper_broker.adapter_id)
broker_registry.register(paper_broker)
broker_registry.register(live_broker)
_paper_state_loaded = False


def _ensure_paper_state() -> None:
    global _paper_state_loaded
    if _paper_state_loaded:
        return
    account = audit_store.load_paper_account()
    if account is not None:
        paper_broker.restore(account)
    _paper_state_loaded = True


class PaperOrderRequest(TradingModel):
    intent: OrderIntent
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)


class ProtectiveExitRequest(TradingModel):
    prices: dict[str, float]
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)


class KillSwitchRequest(TradingModel):
    active: bool


@router.post("/decisions", response_model=DecisionTrace)
async def create_decision(request: DecisionRequest) -> DecisionTrace:
    trace = await decision_service.decide(request)
    audit_store.record_trace(trace)
    return trace


@router.post("/decisions/from-data", response_model=DecisionTrace)
async def create_decision_from_data(request: DecisionFromDataRequest) -> DecisionTrace:
    response = await asyncio.to_thread(
        DailyBarsService().get_daily_bars,
        request.symbol,
        request.start_date.strftime("%Y%m%d"),
        request.end_date.strftime("%Y%m%d"),
    )
    bars = []
    evidence_refs = []
    for record in response.records:
        try:
            trade_date = datetime.strptime(
                str(record.data["trade_date"]).replace("-", ""),
                "%Y%m%d",
            ).date()
            bars.append(
                {
                    "trade_date": trade_date,
                    "open": record.data["open"],
                    "high": record.data["high"],
                    "low": record.data["low"],
                    "close": record.data["close"],
                    "volume": record.data["volume"],
                }
            )
            evidence_refs.append(f"data_record:{record.record_id}")
        except (KeyError, TypeError, ValueError):
            continue
    try:
        decision_request = DecisionRequest(
            symbol=response.symbol,
            bars=bars,
            fundamentals=request.fundamentals,
            portfolio=request.portfolio,
            risk_limits=request.risk_limits,
            evidence_refs=evidence_refs,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"At least 30 valid daily bars are required: {exc}",
        ) from exc
    trace = await decision_service.decide(decision_request)
    audit_store.record_trace(trace)
    return trace


@router.post("/backtests", response_model=BacktestResult)
def backtest(request: BacktestRequest) -> BacktestResult:
    return run_backtest(request)


@router.post("/portfolio/optimize", response_model=OptimizationResult)
def portfolio_optimization(request: OptimizationRequest) -> OptimizationResult:
    return optimize_portfolio(request)


@router.post("/paper/orders", response_model=OrderResult)
def submit_paper_order(request: PaperOrderRequest) -> OrderResult:
    _ensure_paper_state()
    result = paper_broker.submit_order(request.intent, request.risk_limits)
    audit_store.record_order(result)
    audit_store.record_paper_account(paper_broker.account())
    return result


@router.get("/brokers", response_model=BrokerCatalog)
def broker_catalog() -> BrokerCatalog:
    return broker_registry.catalog()


@router.post("/paper/protective-exits", response_model=list[OrderResult])
def execute_protective_exits(request: ProtectiveExitRequest) -> list[OrderResult]:
    _ensure_paper_state()
    results = paper_broker.execute_protective_exits(request.prices, request.risk_limits)
    for result in results:
        audit_store.record_order(result)
    audit_store.record_paper_account(paper_broker.account())
    return results


@router.get("/paper/account", response_model=PaperAccount)
def paper_account() -> PaperAccount:
    _ensure_paper_state()
    return paper_broker.account()


@router.post("/paper/kill-switch", response_model=PaperAccount)
def set_paper_kill_switch(request: KillSwitchRequest) -> PaperAccount:
    _ensure_paper_state()
    paper_broker.kill_switch = request.active
    account = paper_broker.account()
    audit_store.record_paper_account(account)
    return account


@router.post("/live/orders", response_model=OrderResult)
def submit_live_order(request: PaperOrderRequest) -> OrderResult:
    try:
        return live_broker.submit_order(request.intent, request.risk_limits)
    except LiveTradingDisabledError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


@router.get("/reviews/daily/{review_date}", response_model=DailyReview)
def daily_review(review_date: date) -> DailyReview:
    return audit_store.daily_review(review_date)


@router.get("/reviews/daily/{review_date}/markdown", response_class=PlainTextResponse)
def daily_review_markdown(review_date: date) -> str:
    return render_daily_review_markdown(audit_store.daily_review(review_date))
