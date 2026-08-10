from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from trading.experiments.models import (
    ExperimentRiskFlag,
    ExperimentType,
)
from trading.experiments.schemas import (
    CreateExperimentRequest,
    CreateExperimentResponse,
    ForwardReturnUpdateRequest,
    ForwardReturnUpdateResponse,
    HistoricalReplayRequest,
    HistoricalReplayResponse,
    MetricsResponse,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    RunExperimentRequest,
    RunExperimentResponse,
)
from trading.experiments.service import ExperimentEvaluationService


router = APIRouter(tags=["experiment-evaluation"])
service = ExperimentEvaluationService()


def _error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(exc, KeyError) else 422,
        detail=str(exc),
    )


@router.post("/v1/experiments", response_model=CreateExperimentResponse)
def create_experiment(
    request: CreateExperimentRequest,
) -> CreateExperimentResponse:
    try:
        return service.create_experiment(request)
    except ValueError as exc:
        raise _error(exc) from exc


@router.get(
    "/v1/experiments/{experiment_id}",
    response_model=CreateExperimentResponse,
)
def get_experiment(experiment_id: str) -> CreateExperimentResponse:
    try:
        definition = service.get_experiment(experiment_id)
    except KeyError as exc:
        raise _error(exc) from exc
    return CreateExperimentResponse(
        experiment=definition,
        persisted=True,
        existing=True,
        point_in_time_status="REGISTERED",
        sample_count=0,
        data_quality_summary={
            "stable_config_hash": True,
            "production_weight_update": False,
            "trade_execution_enabled": False,
        },
        risk_flags=[
            ExperimentRiskFlag.RESEARCH_ONLY,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
        ],
    )


@router.post(
    "/v1/experiments/{experiment_id}/run",
    response_model=RunExperimentResponse | HistoricalReplayResponse,
)
def run_experiment(
    experiment_id: str,
    request: RunExperimentRequest,
) -> RunExperimentResponse | HistoricalReplayResponse:
    try:
        definition = service.get_experiment(experiment_id)
        if definition.experiment_type == ExperimentType.HISTORICAL_REPLAY:
            return service.replay_historical(
                experiment_id,
                HistoricalReplayRequest(
                    start_trade_date=definition.start_trade_date,
                    end_trade_date=definition.end_trade_date,
                    random_seed=definition.random_seed,
                    persist=request.persist,
                ),
            )
        return service.run_prospective(experiment_id, request)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/v1/experiment-runs/{run_id}", response_model=dict[str, Any])
def get_experiment_run(run_id: str) -> dict[str, Any]:
    try:
        run = service.get_run(run_id)
    except KeyError as exc:
        raise _error(exc) from exc
    return {
        "run": run.model_dump(mode="json"),
        "research_only": True,
        "point_in_time_status": "SNAPSHOT_PRESERVED",
        "sample_count": run.generated_signal_count,
        "data_quality_summary": run.data_quality_summary,
        "risk_flags": [flag.value for flag in run.risk_flags],
        "profitability_proven": False,
    }


@router.post(
    "/v1/evaluations/forward-returns/update",
    response_model=ForwardReturnUpdateResponse,
)
def update_forward_returns(
    request: ForwardReturnUpdateRequest,
) -> ForwardReturnUpdateResponse:
    try:
        return service.update_forward_returns(request)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


@router.get(
    "/v1/experiment-runs/{run_id}/metrics",
    response_model=MetricsResponse,
)
def metrics(run_id: str) -> MetricsResponse:
    try:
        return service.metrics(run_id)
    except KeyError as exc:
        raise _error(exc) from exc


@router.get(
    "/v1/experiment-runs/{run_id}/observations",
    response_model=dict[str, Any],
)
def observations(run_id: str) -> dict[str, Any]:
    try:
        items = service.observations(run_id)
    except KeyError as exc:
        raise _error(exc) from exc
    return {
        "run_id": run_id,
        "observations": [
            item.model_dump(mode="json") for item in items
        ],
        "research_only": True,
        "point_in_time_status": "APPEND_ONLY_SIGNAL_SNAPSHOTS",
        "sample_count": len(items),
        "data_quality_summary": {
            "point_in_time_valid_count": sum(
                item.point_in_time_valid for item in items
            ),
            "stale_count": sum(item.stale_snapshot for item in items),
        },
        "risk_flags": [
            ExperimentRiskFlag.RESEARCH_ONLY.value,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY.value,
        ],
        "profitability_proven": False,
    }


@router.get(
    "/v1/experiment-runs/{run_id}/report",
    response_model=dict[str, Any],
)
def report(run_id: str) -> dict[str, Any]:
    try:
        return service.report(run_id).model_dump(mode="json")
    except KeyError as exc:
        raise _error(exc) from exc


@router.post(
    "/v1/backtests/run",
    response_model=PortfolioBacktestResult,
)
def run_backtest(
    request: PortfolioBacktestRequest,
) -> PortfolioBacktestResult:
    try:
        return service.run_portfolio_backtest(request)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


__all__ = ["router", "service"]
