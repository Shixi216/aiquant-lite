from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.experiments.hashing import stable_hash, stable_id
from trading.experiments.models import (
    SUPPORTED_HORIZONS,
    ExperimentRiskFlag,
    ExperimentStatus,
    ExperimentType,
    LabelStatus,
    RebalanceMethod,
    SignalType,
    WeightingMethod,
)


class ExperimentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, frozen=True)


class ResearchEnvelope(ExperimentModel):
    research_only: Literal[True] = True
    point_in_time_status: str
    sample_count: int = Field(ge=0)
    data_quality_summary: dict[str, Any] = Field(default_factory=dict)
    risk_flags: list[ExperimentRiskFlag] = Field(default_factory=list)
    profitability_proven: Literal[False] = False


class ExperimentDefinition(ExperimentModel):
    experiment_id: str
    experiment_name: str = Field(min_length=1, max_length=240)
    experiment_type: ExperimentType
    description: str
    hypothesis: str
    universe_definition: dict[str, Any]
    query_plan_id: str | None = None
    scanner_version: str | None = None
    orchestration_version: str | None = None
    formal_strategy_version: str = "auditable_multi_factor_v1"
    shadow_strategy_version: str = "shadow-composite-v1"
    signal_frequency: str = "DAILY_AFTER_CLOSE"
    signal_time_policy: str = "AFTER_MARKET_DATA_AVAILABLE"
    entry_policy: str = "NEXT_TRADING_DAY_OPEN"
    exit_policy: str = "HORIZON_TRADING_DAY_CLOSE"
    holding_periods: tuple[int, ...] = SUPPORTED_HORIZONS
    top_k: int = Field(default=20, ge=1, le=100)
    weighting_method: WeightingMethod = WeightingMethod.TOP_K_EQUAL_WEIGHT
    benchmark_definition: str = "ELIGIBLE_UNIVERSE_EQUAL_WEIGHT"
    cost_model: dict[str, Any] | None = None
    missing_data_policy: str = "EXCLUDE_AND_FLAG"
    stale_data_policy: str = "RETAIN_EXCLUDE_PRIMARY"
    minimum_sample_size: int = Field(default=30, ge=1)
    start_trade_date: date | None = None
    end_trade_date: date | None = None
    random_seed: int = 20260730
    config_json: dict[str, Any]
    config_hash: str
    version: int = Field(default=1, ge=1)
    created_at: datetime
    research_only: Literal[True] = True
    production_weight_update: Literal[False] = False
    trade_execution_enabled: Literal[False] = False
    experimental_only: bool = False
    status: ExperimentStatus = ExperimentStatus.REGISTERED

    @model_validator(mode="after")
    def validate_definition(self) -> "ExperimentDefinition":
        if tuple(self.holding_periods) != tuple(sorted(set(self.holding_periods))):
            raise ValueError("holding periods must be unique and sorted")
        if any(item not in SUPPORTED_HORIZONS for item in self.holding_periods):
            raise ValueError("holding periods must be 1, 3, 5, or 20")
        if self.start_trade_date and self.end_trade_date:
            if self.start_trade_date > self.end_trade_date:
                raise ValueError("start_trade_date must not exceed end_trade_date")
        if self.config_hash != stable_hash(self.config_json):
            raise ValueError("config_hash does not match config_json")
        return self


class CreateExperimentRequest(ExperimentModel):
    experiment_name: str = Field(min_length=1, max_length=240)
    experiment_type: ExperimentType
    description: str = ""
    hypothesis: str = ""
    universe_definition: dict[str, Any] = Field(
        default_factory=lambda: {"type": "HISTORICAL_DAILY_BAR_UNIVERSE"}
    )
    query_plan_id: str | None = None
    scanner_version: str | None = "market-scanner-v1"
    orchestration_version: str | None = "five-factor-orchestration-v1"
    signal_frequency: str = "DAILY_AFTER_CLOSE"
    holding_periods: tuple[int, ...] = SUPPORTED_HORIZONS
    top_k: int = Field(default=20, ge=1, le=100)
    weighting_method: WeightingMethod = WeightingMethod.TOP_K_EQUAL_WEIGHT
    benchmark_definition: str = "ELIGIBLE_UNIVERSE_EQUAL_WEIGHT"
    cost_model: dict[str, Any] | None = None
    missing_data_policy: str = "EXCLUDE_AND_FLAG"
    stale_data_policy: str = "RETAIN_EXCLUDE_PRIMARY"
    minimum_sample_size: int = Field(default=30, ge=1)
    start_trade_date: date | None = None
    end_trade_date: date | None = None
    random_seed: int = 20260730
    config: dict[str, Any] = Field(default_factory=dict)
    experimental_only: bool = False
    persist: bool = False

    @field_validator("holding_periods")
    @classmethod
    def validate_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if any(item not in SUPPORTED_HORIZONS for item in normalized):
            raise ValueError("holding periods must be 1, 3, 5, or 20")
        return normalized


class CreateExperimentResponse(ResearchEnvelope):
    experiment: ExperimentDefinition
    persisted: bool
    existing: bool = False


class ExperimentRun(ExperimentModel):
    run_id: str
    experiment_id: str
    run_type: str
    started_at: datetime
    completed_at: datetime | None = None
    code_version: str
    git_head: str
    migration_version: str = "0111_experiment_evaluation_v1"
    database_snapshot_hash: str
    dataset_snapshot_hash: str
    input_snapshot_hash: str
    query_plan_hash: str
    data_cutoff: datetime
    as_of_trade_date: date | None = None
    available_history_start: date | None = None
    available_history_end: date | None = None
    eligible_signal_date_count: int = Field(ge=0)
    generated_signal_count: int = Field(ge=0)
    labeled_signal_count: int = Field(ge=0)
    pending_label_count: int = Field(ge=0)
    invalid_signal_count: int = Field(ge=0)
    benchmark_available: bool
    elapsed_ms: float = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    database_query_count: int = Field(ge=0)
    network_request_count: Literal[0] = 0
    model_call_count: Literal[0] = 0
    status: ExperimentStatus
    risk_flags: list[ExperimentRiskFlag]
    data_quality_summary: dict[str, Any]
    sanitized_error: str | None = None
    generated_at: datetime
    research_only: Literal[True] = True
    profitability_proven: Literal[False] = False


class ExperimentObservation(ExperimentModel):
    observation_id: str
    run_id: str
    signal_id: str
    signal_type: SignalType
    signal_trade_date: date
    signal_generated_at: datetime
    data_cutoff: datetime
    actionable_from_trade_date: date | None = None
    symbol: str
    board: str | None = None
    industry: str | None = None
    rank: int = Field(ge=1)
    scanner_score: float | None = None
    technical_score: float | None = None
    fundamental_score: float | None = None
    formal_score: float | None = None
    formal_action: str | None = None
    shadow_score: float | None = None
    composite_confidence: float | None = None
    factor_coverage: str = "1/5"
    available_factors: list[str] = Field(default_factory=list)
    missing_factors: list[str] = Field(default_factory=list)
    anomaly_types: list[str] = Field(default_factory=list)
    risk_flags: list[ExperimentRiskFlag] = Field(default_factory=list)
    veto_status: str = "NOT_EVALUATED"
    query_plan_hash: str
    signal_snapshot_hash: str
    source_run_id: str | None = None
    stale_snapshot: bool = False
    point_in_time_valid: bool = True
    is_suspended: bool | None = None
    data_complete: bool = True
    created_at: datetime


class ForwardReturnLabel(ExperimentModel):
    label_id: str
    observation_id: str
    symbol: str
    signal_trade_date: date
    entry_trade_date: date | None = None
    exit_trade_date: date | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    entry_price_type: Literal["NEXT_TRADING_DAY_OPEN"] = "NEXT_TRADING_DAY_OPEN"
    exit_price_type: Literal["HORIZON_TRADING_DAY_CLOSE"] = (
        "HORIZON_TRADING_DAY_CLOSE"
    )
    gross_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    maximum_favorable_excursion: float | None = None
    maximum_adverse_excursion: float | None = None
    was_suspended_on_entry: bool | None = None
    was_suspended_during_holding: bool | None = None
    was_price_limit_locked: bool | None = None
    missing_price_reason: str | None = None
    return_basis: Literal["RAW_PRICE_RETURN"] = "RAW_PRICE_RETURN"
    horizon_trading_days: Literal[1, 3, 5, 20]
    label_status: LabelStatus
    risk_flags: list[ExperimentRiskFlag] = Field(default_factory=list)
    calculated_at: datetime | None = None
    label_version: str = "forward-return-raw-v1"
    generated_at: datetime


class RunExperimentRequest(ExperimentModel):
    source_scanner_run_id: str | None = None
    data_cutoff: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    persist: bool = False


class RunExperimentResponse(ResearchEnvelope):
    run: ExperimentRun
    observations: list[ExperimentObservation]
    labels: list[ForwardReturnLabel] = Field(default_factory=list)
    persisted_observation_count: int = 0
    persisted_label_count: int = 0


class ForwardReturnUpdateRequest(ExperimentModel):
    run_id: str | None = None
    experiment_id: str | None = None
    as_of: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    persist: bool = False

    @model_validator(mode="after")
    def require_scope(self) -> "ForwardReturnUpdateRequest":
        if (self.run_id is None) == (self.experiment_id is None):
            raise ValueError("provide exactly one of run_id or experiment_id")
        return self


class ForwardReturnUpdateResponse(ResearchEnvelope):
    labels: list[ForwardReturnLabel]
    updated_count: int
    unchanged_count: int
    status: str


class HistoricalReplayRequest(ExperimentModel):
    start_trade_date: date | None = None
    end_trade_date: date | None = None
    top_k: Literal[20] = 20
    random_seed: int = 20260730
    persist: bool = False


class HistoricalWindow(ExperimentModel):
    available_history_start: date | None
    available_history_end: date | None
    distinct_trade_dates: int
    maximum_feature_lookback: int = 20
    maximum_forward_horizon: int = 20
    warmup_trading_days: int = 1
    required_history: int = 41
    earliest_signal_trade_date: date | None
    latest_signal_trade_date_1d: date | None
    latest_signal_trade_date_3d: date | None
    latest_signal_trade_date_5d: date | None
    latest_signal_trade_date_20d: date | None
    full_horizon_signal_date_count: int
    partial_replay: bool = True


class HistoricalReplayResponse(RunExperimentResponse):
    historical_window: HistoricalWindow
    signal_date_count: int
    symbol_count: int
    query_count: int
    network_request_count: Literal[0] = 0
    model_call_count: Literal[0] = 0


class SignalMetricSet(ExperimentModel):
    group_name: str
    horizon_trading_days: int
    sample_count: int
    labeled_count: int
    pending_count: int
    valid_count: int
    win_rate: float | None = None
    mean_return: float | None = None
    median_return: float | None = None
    return_std: float | None = None
    positive_excess_rate: float | None = None
    mean_excess_return: float | None = None
    median_excess_return: float | None = None
    maximum_favorable_excursion: float | None = None
    maximum_adverse_excursion: float | None = None
    rank_return_spearman: float | None = None
    top_k_hit_rate: float | None = None
    confidence_interval: tuple[float, float] | None = None
    risk_flags: list[ExperimentRiskFlag] = Field(default_factory=list)


class MetricsResponse(ResearchEnvelope):
    run_id: str
    metrics: list[SignalMetricSet]
    slices: list[dict[str, Any]] = Field(default_factory=list)


class PortfolioBacktestRequest(ExperimentModel):
    experiment_id: str
    run_id: str
    signal_type: SignalType = SignalType.SCANNER_ONLY
    horizon_trading_days: Literal[1, 3, 5, 20] = 5
    top_k: int = Field(default=20, ge=1, le=100)
    weighting_method: WeightingMethod = WeightingMethod.TOP_K_EQUAL_WEIGHT
    rebalance_method: RebalanceMethod = RebalanceMethod.NON_OVERLAPPING_COHORT
    max_position_weight: float = Field(default=0.10, gt=0, le=1)
    cost_model: dict[str, Any] | None = None
    persist: bool = False


class PortfolioBacktestResult(ResearchEnvelope):
    backtest_id: str
    experiment_id: str
    run_id: str
    weighting_method: WeightingMethod
    rebalance_method: RebalanceMethod
    horizon_trading_days: int
    top_k: int
    max_position_weight: float
    cumulative_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    maximum_drawdown: float | None = None
    sharpe_ratio: float | None = None
    downside_deviation: float | None = None
    turnover: float | None = None
    gross_return: float | None = None
    net_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    information_ratio: float | None = None
    active_days: int = 0
    positions: list[dict[str, Any]] = Field(default_factory=list)
    persisted: bool = False
    trade_execution_enabled: Literal[False] = False
    paper_trading_written: Literal[False] = False


class ExperimentReportResponse(ResearchEnvelope):
    experiment: ExperimentDefinition
    run: ExperimentRun
    metrics: list[SignalMetricSet]
    narrative: dict[str, Any]


def build_definition(
    request: CreateExperimentRequest,
    *,
    created_at: datetime,
) -> ExperimentDefinition:
    config = {
        "experiment_name": request.experiment_name,
        "experiment_type": request.experiment_type.value,
        "description": request.description,
        "hypothesis": request.hypothesis,
        "universe_definition": request.universe_definition,
        "query_plan_id": request.query_plan_id,
        "scanner_version": request.scanner_version,
        "orchestration_version": request.orchestration_version,
        "formal_strategy_version": "auditable_multi_factor_v1",
        "shadow_strategy_version": "shadow-composite-v1",
        "signal_frequency": request.signal_frequency,
        "signal_time_policy": "AFTER_MARKET_DATA_AVAILABLE",
        "entry_policy": "NEXT_TRADING_DAY_OPEN",
        "exit_policy": "HORIZON_TRADING_DAY_CLOSE",
        "holding_periods": list(request.holding_periods),
        "top_k": request.top_k,
        "weighting_method": request.weighting_method.value,
        "benchmark_definition": request.benchmark_definition,
        "cost_model": request.cost_model,
        "missing_data_policy": request.missing_data_policy,
        "stale_data_policy": request.stale_data_policy,
        "minimum_sample_size": request.minimum_sample_size,
        "start_trade_date": request.start_trade_date,
        "end_trade_date": request.end_trade_date,
        "random_seed": request.random_seed,
        "experimental_only": request.experimental_only,
        "parameters": request.config,
        "research_only": True,
        "production_weight_update": False,
        "trade_execution_enabled": False,
    }
    config_hash = stable_hash(config)
    return ExperimentDefinition(
        experiment_id=stable_id("exp", config),
        experiment_name=request.experiment_name,
        experiment_type=request.experiment_type,
        description=request.description,
        hypothesis=request.hypothesis,
        universe_definition=request.universe_definition,
        query_plan_id=request.query_plan_id,
        scanner_version=request.scanner_version,
        orchestration_version=request.orchestration_version,
        signal_frequency=request.signal_frequency,
        holding_periods=request.holding_periods,
        top_k=request.top_k,
        weighting_method=request.weighting_method,
        benchmark_definition=request.benchmark_definition,
        cost_model=request.cost_model,
        missing_data_policy=request.missing_data_policy,
        stale_data_policy=request.stale_data_policy,
        minimum_sample_size=request.minimum_sample_size,
        start_trade_date=request.start_trade_date,
        end_trade_date=request.end_trade_date,
        random_seed=request.random_seed,
        config_json=config,
        config_hash=config_hash,
        created_at=created_at,
        experimental_only=request.experimental_only,
    )


__all__ = [
    "CreateExperimentRequest",
    "CreateExperimentResponse",
    "ExperimentDefinition",
    "ExperimentObservation",
    "ExperimentReportResponse",
    "ExperimentRun",
    "ForwardReturnLabel",
    "ForwardReturnUpdateRequest",
    "ForwardReturnUpdateResponse",
    "HistoricalReplayRequest",
    "HistoricalReplayResponse",
    "HistoricalWindow",
    "MetricsResponse",
    "PortfolioBacktestRequest",
    "PortfolioBacktestResult",
    "ResearchEnvelope",
    "RunExperimentRequest",
    "RunExperimentResponse",
    "SignalMetricSet",
    "build_definition",
]
