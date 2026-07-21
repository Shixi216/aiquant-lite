from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    VETO = "veto"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    REJECTED = "rejected"
    FILLED = "filled"


class BrokerMode(StrEnum):
    PAPER = "paper"
    LIVE_DISABLED = "live_disabled"


class BrokerCapabilities(TradingModel):
    adapter_id: str
    broker_name: str
    mode: BrokerMode
    status: str
    available: bool
    execution_enabled: bool
    environment_probed: bool = False
    credentials_stored: bool = False
    supported_operations: list[str] = Field(default_factory=list)
    planned_operations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    security_guards: list[str] = Field(default_factory=list)


class BrokerCatalog(TradingModel):
    active_adapter_id: str
    adapters: list[BrokerCapabilities]


class CancelOrderResult(TradingModel):
    adapter_id: str
    client_order_id: str
    accepted: bool
    reason: str


class Bar(TradingModel):
    trade_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Bar":
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high/low does not contain open and close")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        return self


class FundamentalSnapshot(TradingModel):
    as_of: date
    pe_ttm: float | None = None
    pb: float | None = None
    roe: float | None = None
    revenue_growth: float | None = None
    debt_ratio: float | None = Field(default=None, ge=0)
    operating_cash_flow_positive: bool | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)


class Signal(TradingModel):
    role: str
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    summary: str
    evidence: list[str] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=30)


class AgentOpinion(TradingModel):
    role: str
    stance: Literal["bullish", "neutral", "bearish", "veto"]
    summary: str
    evidence: list[str] = Field(default_factory=list, max_length=50)
    counterpoints: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)


class StrategyProposal(TradingModel):
    name: str
    action: Action
    target_weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    entry_rule: str
    exit_rule: str
    invalidation_rules: list[str]
    evidence: list[str] = Field(default_factory=list)


class RiskLimits(TradingModel):
    max_order_notional: float = Field(default=100_000, gt=0)
    max_position_weight: float = Field(default=0.10, gt=0, le=1)
    max_gross_exposure: float = Field(default=0.80, gt=0, le=1)
    min_cash_weight: float = Field(default=0.10, ge=0, lt=1)
    max_daily_loss: float = Field(default=0.02, gt=0, lt=1)
    stop_loss_pct: float = Field(default=0.07, gt=0, lt=1)
    take_profit_pct: float = Field(default=0.15, gt=0)
    max_price_age_seconds: int = Field(default=30, ge=1)
    require_human_approval: bool = True


class PortfolioState(TradingModel):
    cash: float = Field(ge=0)
    equity: float = Field(gt=0)
    gross_exposure: float = Field(default=0, ge=0)
    current_weight: float = Field(default=0, ge=0, le=1)
    daily_pnl_pct: float = Field(default=0, ge=-1)
    kill_switch: bool = False


class RiskVerdict(TradingModel):
    approved: bool
    vetoed: bool
    reasons: list[str] = Field(default_factory=list)
    adjusted_target_weight: float = Field(ge=0, le=1)
    human_approval_required: bool


class AdversarialReview(TradingModel):
    passed: bool
    findings: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)


class DecisionRequest(TradingModel):
    symbol: str = Field(min_length=6, max_length=16)
    bars: list[Bar] = Field(min_length=30)
    fundamentals: FundamentalSnapshot | None = None
    portfolio: PortfolioState
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    evidence_refs: list[str] = Field(default_factory=list)


class DecisionFromDataRequest(TradingModel):
    symbol: str = Field(min_length=6, max_length=16)
    start_date: date
    end_date: date
    fundamentals: FundamentalSnapshot | None = None
    portfolio: PortfolioState
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)

    @model_validator(mode="after")
    def validate_dates(self) -> "DecisionFromDataRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class DecisionTrace(TradingModel):
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    symbol: str
    as_of: date
    opinions: list[AgentOpinion]
    proposal: StrategyProposal
    risk_verdict: RiskVerdict
    adversarial_review: AdversarialReview
    final_action: Action
    final_target_weight: float = Field(ge=0, le=1)
    rationale_summary: list[str]
    evidence_refs: list[str]
    audit_notice: str = (
        "This trace stores evidence and concise decision summaries, not hidden chain-of-thought."
    )


class OrderIntent(TradingModel):
    client_order_id: str = Field(default_factory=lambda: uuid4().hex)
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    reference_price: float = Field(gt=0)
    price_time: datetime
    approved_by: str | None = None


class OrderResult(TradingModel):
    order_id: str = Field(default_factory=lambda: uuid4().hex)
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    status: OrderStatus
    fill_price: float | None = None
    fee: float = Field(default=0, ge=0)
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class Position(TradingModel):
    symbol: str
    quantity: int = Field(ge=0)
    average_cost: float = Field(ge=0)
    last_price: float = Field(ge=0)
    realized_pnl: float = 0


class BrokerAccountSnapshot(TradingModel):
    cash: float = Field(ge=0)
    positions: dict[str, Position]
    equity: float = Field(ge=0)


class PaperAccount(BrokerAccountSnapshot):
    kill_switch: bool


class BacktestRequest(TradingModel):
    symbol: str
    bars: list[Bar] = Field(min_length=60)
    initial_cash: float = Field(default=1_000_000, gt=0)
    commission_rate: float = Field(default=0.0003, ge=0, le=0.01)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    max_position_weight: float = Field(default=0.50, gt=0, le=1)


class BacktestTrade(TradingModel):
    trade_date: date
    side: OrderSide
    quantity: int
    price: float
    fee: float
    reason: str


class BacktestResult(TradingModel):
    symbol: str
    initial_cash: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: list[BacktestTrade]
    equity_curve: list[tuple[date, float]]
    assumptions: list[str]


class OptimizationInput(TradingModel):
    symbol: str
    expected_score: float = Field(ge=-1, le=1)
    volatility: float = Field(gt=0)


class OptimizationRequest(TradingModel):
    assets: list[OptimizationInput] = Field(min_length=1)
    max_position_weight: float = Field(default=0.20, gt=0, le=1)
    max_gross_exposure: float = Field(default=0.80, gt=0, le=1)


class OptimizationResult(TradingModel):
    weights: dict[str, float]
    cash_weight: float
    method: str = "bounded_positive_score_inverse_volatility"


class DailyReview(TradingModel):
    review_date: date
    traces: list[DecisionTrace]
    orders: list[OrderResult]
    veto_count: int
    summary: list[str]
    audit_notice: str = "Structured audit summaries are shown; hidden chain-of-thought is excluded."
