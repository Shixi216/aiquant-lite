from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.version import ROUTER_GENERATOR_VERSION


class TradingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    VETO = "veto"


class AnalysisMode(StrEnum):
    SCREENING = "SCREENING"
    RESEARCH = "RESEARCH"
    DECISION = "DECISION"


class FundamentalDataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    REJECTED = "rejected"
    FILLED = "filled"


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
    net_profit_growth: float | None = None
    debt_ratio: float | None = Field(default=None, ge=0)
    operating_cash_flow: float | None = None
    operating_cash_flow_positive: bool | None = None
    peg: float | None = None
    report_period: str | None = None
    statement_types: list[str] = Field(default_factory=list)
    announcement_time: datetime | None = None
    data_available_time: datetime | None = None
    primary_source: str | None = None
    verification_status: str | None = None
    source_type: str | None = None
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
    factor_output_ids: list[str] = Field(default_factory=list)


class DecisionFromDataRequest(TradingModel):
    symbol: str = Field(min_length=6, max_length=16)
    start_date: date
    end_date: date
    fundamentals: FundamentalSnapshot | None = None
    portfolio: PortfolioState
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    factor_output_ids: list[str] = Field(default_factory=list)
    analysis_mode: AnalysisMode = AnalysisMode.DECISION
    data_cutoff: datetime | None = None
    auto_fetch_fundamental: bool = True
    strict_point_in_time: bool = True
    allow_user_fundamental_override: bool = False
    user_fundamental_override_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
    )
    user_fundamental_operator_confirmed: bool = False

    @model_validator(mode="after")
    def validate_dates(self) -> "DecisionFromDataRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.data_cutoff is not None:
            if (
                self.data_cutoff.tzinfo is None
                or self.data_cutoff.utcoffset() is None
            ):
                raise ValueError("data_cutoff must include a timezone")
            if self.end_date > self.data_cutoff.date():
                raise ValueError("end_date must not be later than data_cutoff")
        if self.analysis_mode == AnalysisMode.DECISION:
            if not self.strict_point_in_time:
                raise ValueError(
                    "DECISION requires strict_point_in_time=true"
                )
        if self.allow_user_fundamental_override:
            if self.fundamentals is None:
                raise ValueError(
                    "manual override requires fundamentals"
                )
            if not self.user_fundamental_override_reason:
                raise ValueError(
                    "manual override requires an override reason"
                )
            if not self.user_fundamental_operator_confirmed:
                raise ValueError(
                    "manual override requires operator confirmation"
                )
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


class DecisionStatus(StrEnum):
    DRAFT = "DRAFT"
    FINAL = "FINAL"
    SUPERSEDED = "SUPERSEDED"


class VerifiedFact(TradingModel):
    fact: str = Field(min_length=1)
    value: Any
    as_of: datetime
    source_record_ids: list[str] = Field(min_length=1)


class ModelInference(TradingModel):
    agent_role: str = Field(min_length=1)
    inference: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    supporting_source_record_ids: list[str] = Field(default_factory=list)


class DecisionScenario(TradingModel):
    summary: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)


class DecisionPacket(TradingModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(min_length=1)
    decision_version: int = Field(ge=1)
    supersedes_version: int | None = Field(default=None, ge=1)
    symbol: str = Field(min_length=6, max_length=16)
    generated_at: datetime
    data_cutoff_time: datetime
    packet_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = "1.0"
    generator_version: str = ROUTER_GENERATOR_VERSION
    status: DecisionStatus
    decision: DecisionTrace
    confidence: float = Field(ge=0, le=1)
    evidence_quality: float = Field(ge=0, le=1)
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    calculated_metrics: dict[str, float] = Field(default_factory=dict)
    model_inferences: list[ModelInference] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    bull_case: DecisionScenario
    base_case: DecisionScenario
    bear_case: DecisionScenario
    entry_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    suggested_position_limit: float = Field(ge=0, le=1)
    holding_horizon: str = Field(min_length=1)
    missing_information: list[str] = Field(default_factory=list)
    agent_disagreements: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(min_length=1)
    factor_output_ids: list[str] = Field(default_factory=list)
    model_call_ids: list[str] = Field(default_factory=list)
    analysis_mode: AnalysisMode = AnalysisMode.DECISION
    fundamental_data_status: FundamentalDataStatus | None = None
    used_report_period: str | None = None
    announcement_time: datetime | None = None
    data_available_time: datetime | None = None
    missing_fields: list[str] = Field(default_factory=list)
    factor_output_id: str | None = None
    verification_status: str | None = None
    auto_fetch_attempted: bool = False
    auto_fetch_result: str | None = None
    manual_fundamental_audit_id: str | None = None

    @model_validator(mode="after")
    def validate_packet_invariants(self) -> "DecisionPacket":
        generated_at = (
            self.generated_at
            if self.generated_at.tzinfo
            else self.generated_at.astimezone()
        )
        data_cutoff_time = (
            self.data_cutoff_time
            if self.data_cutoff_time.tzinfo
            else self.data_cutoff_time.astimezone()
        )
        if data_cutoff_time > generated_at:
            raise ValueError("data_cutoff_time must not be later than generated_at")
        if self.decision_version == 1 and self.supersedes_version is not None:
            raise ValueError("decision version 1 cannot supersede another version")
        if self.decision_version > 1:
            if self.supersedes_version is None:
                raise ValueError("new decision versions must set supersedes_version")
            if self.supersedes_version >= self.decision_version:
                raise ValueError("supersedes_version must be lower than decision_version")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("source_record_ids must be unique")
        if len(self.factor_output_ids) != len(set(self.factor_output_ids)):
            raise ValueError("factor_output_ids must be unique")
        if len(self.model_call_ids) != len(set(self.model_call_ids)):
            raise ValueError("model_call_ids must be unique")
        known_sources = set(self.source_record_ids)
        for fact in self.verified_facts:
            if not set(fact.source_record_ids) <= known_sources:
                raise ValueError("verified facts must reference packet source_record_ids")
        for inference in self.model_inferences:
            if not set(inference.supporting_source_record_ids) <= known_sources:
                raise ValueError("model inferences reference unknown source records")
        return self

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json", exclude={"packet_hash"})
        # Preserve hashes of packets produced before factor references existed.
        # New packets include this field in their hash only when references exist.
        if not payload["factor_output_ids"]:
            payload.pop("factor_output_ids")
        fundamental_audit_fields = {
            "analysis_mode",
            "fundamental_data_status",
            "used_report_period",
            "announcement_time",
            "data_available_time",
            "missing_fields",
            "factor_output_id",
            "verification_status",
            "auto_fetch_attempted",
            "auto_fetch_result",
            "manual_fundamental_audit_id",
        }
        if (
            self.analysis_mode == AnalysisMode.DECISION
            and self.fundamental_data_status is None
            and self.used_report_period is None
            and self.announcement_time is None
            and self.data_available_time is None
            and not self.missing_fields
            and self.factor_output_id is None
            and self.verification_status is None
            and not self.auto_fetch_attempted
            and self.auto_fetch_result is None
            and self.manual_fundamental_audit_id is None
        ):
            for field in fundamental_audit_fields:
                payload.pop(field)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def calculate_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def hash_is_valid(self) -> bool:
        return self.packet_hash == self.calculate_hash()

    @classmethod
    def create(cls, **values: Any) -> "DecisionPacket":
        values["packet_hash"] = "0" * 64
        candidate = cls.model_validate(values)
        values["packet_hash"] = candidate.calculate_hash()
        return cls.model_validate(values)

    def rehashed_copy(self, **updates: Any) -> "DecisionPacket":
        values = self.model_dump()
        values.update(updates)
        return self.create(**values)


class DecisionChallengeRequest(TradingModel):
    challenge: str = Field(min_length=1, max_length=4000)
    data_cutoff_time: datetime | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    model_call_ids: list[str] = Field(default_factory=list)
    create_new_version: bool = False


class DecisionChallengeResult(TradingModel):
    challenge_id: str
    decision_id: str
    challenged_version: int
    adversarial_review: AdversarialReview
    recommendation_only: bool = True
    created_version: int | None = None
    new_packet: DecisionPacket | None = None
    data_cutoff_time: datetime
    source_record_ids: list[str]
    model_call_ids: list[str]
    created_at: datetime


class ManualPreviewStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ManualTradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ManualTradeSource(StrEnum):
    USER_REPORTED = "USER_REPORTED"
    USER_IMPORTED = "USER_IMPORTED"


class ManualVerificationStatus(StrEnum):
    USER_REPORTED = "USER_REPORTED"


class ManualLedgerEventType(StrEnum):
    TRADE = "TRADE"
    CORRECTION = "CORRECTION"
    REVERSAL = "REVERSAL"


class ManualTradeInput(TradingModel):
    portfolio_id: str = Field(min_length=1, max_length=128)
    client_trade_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=6, max_length=16)
    side: ManualTradeSide
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
    traded_at: datetime
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: ManualTradeSource = ManualTradeSource.USER_REPORTED
    notes: str | None = Field(default=None, max_length=2000)


class ManualTradeReplacement(TradingModel):
    client_trade_id: str = Field(min_length=1, max_length=128)
    side: ManualTradeSide
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
    traded_at: datetime
    notes: str | None = Field(default=None, max_length=2000)


class ManualTradeCorrectionRequest(TradingModel):
    correction_type: Literal["CORRECTION", "REVERSAL"]
    reason: str = Field(min_length=1, max_length=2000)
    client_trade_id: str | None = Field(default=None, min_length=1, max_length=128)
    replacement: ManualTradeReplacement | None = None
    expires_in_seconds: int = Field(default=600, ge=60, le=1800)

    @model_validator(mode="after")
    def validate_correction_shape(self) -> "ManualTradeCorrectionRequest":
        if self.correction_type == "CORRECTION" and self.replacement is None:
            raise ValueError("CORRECTION requires a replacement trade")
        if self.correction_type == "REVERSAL" and self.replacement is not None:
            raise ValueError("REVERSAL cannot include a replacement trade")
        if self.correction_type == "REVERSAL" and self.client_trade_id is None:
            raise ValueError("REVERSAL requires client_trade_id")
        return self


class ManualTradePreviewPayload(TradingModel):
    event_type: ManualLedgerEventType
    trade: ManualTradeInput
    correction_of_trade_id: str | None = None
    correction_reason: str | None = None

    @model_validator(mode="after")
    def validate_event_shape(self) -> "ManualTradePreviewPayload":
        if self.event_type == ManualLedgerEventType.TRADE:
            if self.correction_of_trade_id is not None or self.correction_reason is not None:
                raise ValueError("TRADE preview cannot reference a correction")
        elif not self.correction_of_trade_id or not self.correction_reason:
            raise ValueError("correction previews require target trade and reason")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def calculate_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ManualTradePreviewCreateRequest(TradingModel):
    trade: ManualTradeInput
    expires_in_seconds: int = Field(default=600, ge=60, le=1800)


class ManualTradePreview(TradingModel):
    confirmation_id: str
    requested_by: str
    channel: str
    payload: ManualTradePreviewPayload
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    status: ManualPreviewStatus
    created_at: datetime
    confirmed_at: datetime | None = None


class ManualTrade(TradingModel):
    trade_id: str
    portfolio_id: str
    client_trade_id: str
    symbol: str
    side: ManualTradeSide
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    fees: float
    taxes: float
    traded_at: datetime
    decision_id: str | None = None
    source: ManualTradeSource
    verification_status: ManualVerificationStatus = (
        ManualVerificationStatus.USER_REPORTED
    )
    user_confirmed: bool
    created_at: datetime
    created_by: str
    correction_of_trade_id: str | None = None
    notes: str | None = None


class ManualTradeConfirmation(TradingModel):
    preview: ManualTradePreview
    trade: ManualTrade
    idempotent_replay: bool = False


class ManualPosition(TradingModel):
    position_id: str
    portfolio_id: str
    symbol: str
    quantity: int
    average_cost: float = Field(ge=0)
    realized_pnl: float
    total_fees: float
    total_taxes: float
    effective_trade_count: int = Field(ge=0)
    source_trade_ids: list[str]
    last_traded_at: datetime


class ManualPositionRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OriginalThesisStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    WEAKENED = "WEAKENED"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


class ManualRiskRecommendedAction(StrEnum):
    CONTINUE_OBSERVATION = "CONTINUE_OBSERVATION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CONSIDER_REDUCING = "CONSIDER_REDUCING"
    CONSIDER_EXITING = "CONSIDER_EXITING"


class ManualPositionRiskReviewRequest(TradingModel):
    lookback_days: int = Field(default=365, ge=30, le=1095)
    allow_model_review: bool = True


class ManualPositionRiskReview(TradingModel):
    review_id: str
    position_id: str
    reviewed_at: datetime
    data_cutoff_time: datetime
    risk_level: ManualPositionRiskLevel
    original_thesis_status: OriginalThesisStatus
    triggered_rules: list[str]
    new_verified_facts: list[VerifiedFact]
    model_inferences: list[ModelInference]
    missing_information: list[str]
    evidence_record_ids: list[str]
    model_call_ids: list[str]
    recommended_action: ManualRiskRecommendedAction


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


class PaperAccount(TradingModel):
    cash: float = Field(ge=0)
    positions: dict[str, Position]
    equity: float = Field(ge=0)
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
    decision_packets: list[DecisionPacket] = Field(default_factory=list)
    manual_trades: list[ManualTrade] = Field(default_factory=list)
    manual_positions: list[ManualPosition] = Field(default_factory=list)
    manual_position_risk_reviews: list[ManualPositionRiskReview] = Field(
        default_factory=list
    )
    decision_deviations: list[str] = Field(default_factory=list)
    model_errors: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    data_quality_issues: list[str] = Field(default_factory=list)
    audit_notice: str = "Structured audit summaries are shown; hidden chain-of-thought is excluded."
