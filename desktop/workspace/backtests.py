from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from trading.experiments.models import WeightingMethod
from trading.experiments.repository import ExperimentRepository
from trading.experiments.schemas import PortfolioBacktestRequest
from trading.experiments.service import ExperimentEvaluationService


class BacktestWorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    symbols: list[str] = Field(min_length=1, max_length=5000)
    start_date: date
    end_date: date
    signal_frequency: str = "DAILY"
    top_k: int = Field(default=20, ge=1, le=100)
    holding_period: int = Field(default=5, ge=1, le=250)
    weighting_method: str = "EQUAL"
    benchmark: str = "000300.SH"
    commission_rate: float = Field(default=0.0003, ge=0, le=0.01)
    slippage_bps: float = Field(default=5, ge=0, le=100)


@dataclass(frozen=True, slots=True)
class BacktestWorkspaceResult:
    status: str
    config: BacktestWorkspaceConfig
    metrics: dict[str, Any]
    equity_curve: tuple[tuple[str, float], ...]
    drawdown_curve: tuple[tuple[str, float], ...]
    positions: tuple[dict[str, Any], ...]
    data_quality: dict[str, Any]
    risk_flags: tuple[str, ...] = (
        "RAW_PRICE_RISK",
        "CORPORATE_ACTION_RISK",
        "SURVIVORSHIP_BIAS_RISK",
    )
    research_only: bool = True
    profitability_proven: bool = False
    manual_ledger_written: bool = False
    paper_trading_written: bool = False


class BacktestWorkspaceService:
    def __init__(
        self,
        *,
        repository: ExperimentRepository | None = None,
        evaluation: ExperimentEvaluationService | None = None,
    ) -> None:
        self.cancel_requested = False
        self.repository = repository or ExperimentRepository()
        self.evaluation = evaluation or ExperimentEvaluationService(
            repository=self.repository
        )

    def cancel(self) -> None:
        self.cancel_requested = True

    def latest_experiment_id(self) -> str | None:
        return self.repository.latest_experiment_id()

    def run_existing(
        self,
        config: BacktestWorkspaceConfig,
    ) -> BacktestWorkspaceResult:
        if config.end_date < config.start_date:
            raise ValueError("end_date must not precede start_date")
        if self.cancel_requested:
            self.cancel_requested = False
            return BacktestWorkspaceResult(
                status="CANCELLED",
                config=config,
                metrics={},
                equity_curve=(),
                drawdown_curve=(),
                positions=(),
                data_quality={"sample_count": 0, "data_window": None},
            )
        run = self.repository.latest_run(config.experiment_id)
        if run is None:
            raise ValueError("EXPERIMENT_RUN_NOT_FOUND")
        weighting = (
            WeightingMethod.TOP_K_EQUAL_WEIGHT
            if config.weighting_method == "EQUAL"
            else WeightingMethod.SCORE_PROPORTIONAL
        )
        result = self.evaluation.run_portfolio_backtest(
            PortfolioBacktestRequest(
                experiment_id=config.experiment_id,
                run_id=run.run_id,
                horizon_trading_days=(
                    config.holding_period
                    if config.holding_period in {1, 3, 5, 20}
                    else 5
                ),
                top_k=config.top_k,
                weighting_method=weighting,
                persist=False,
            )
        )
        metrics = {
            key: getattr(result, key)
            for key in (
                "cumulative_return",
                "annualized_return",
                "annualized_volatility",
                "maximum_drawdown",
                "sharpe_ratio",
                "turnover",
                "net_return",
                "benchmark_return",
                "excess_return",
                "active_days",
            )
        }
        return BacktestWorkspaceResult(
            status=(
                "COMPLETED"
                if result.active_days > 0
                else "INSUFFICIENT_DATA"
            ),
            config=config,
            metrics=metrics,
            equity_curve=(),
            drawdown_curve=(),
            positions=tuple(result.positions),
            data_quality={
                "sample_count": result.active_days,
                "data_window": [
                    config.start_date.isoformat(),
                    config.end_date.isoformat(),
                ],
                "adjustment_type": "RAW",
                "persisted": result.persisted,
            },
        )

    def summarize_existing(
        self,
        config: BacktestWorkspaceConfig,
        *,
        metrics: dict[str, Any],
        equity_curve: list[tuple[str, float]],
        positions: list[dict[str, Any]],
        sample_count: int,
    ) -> BacktestWorkspaceResult:
        if config.end_date < config.start_date:
            raise ValueError("end_date must not precede start_date")
        if self.cancel_requested:
            return BacktestWorkspaceResult(
                status="CANCELLED",
                config=config,
                metrics={},
                equity_curve=(),
                drawdown_curve=(),
                positions=(),
                data_quality={"sample_count": 0, "data_window": None},
            )
        peak = 0.0
        drawdown: list[tuple[str, float]] = []
        for day, value in equity_curve:
            peak = max(peak, value)
            drawdown.append((day, value / peak - 1 if peak else 0.0))
        return BacktestWorkspaceResult(
            status="COMPLETED",
            config=config,
            metrics=metrics,
            equity_curve=tuple(equity_curve),
            drawdown_curve=tuple(drawdown),
            positions=tuple(positions),
            data_quality={
                "sample_count": sample_count,
                "data_window": [
                    config.start_date.isoformat(),
                    config.end_date.isoformat(),
                ],
                "adjustment_type": "RAW",
            },
        )


__all__ = [
    "BacktestWorkspaceConfig",
    "BacktestWorkspaceResult",
    "BacktestWorkspaceService",
]
