from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from trading.research.capital_flow.evaluation import CapitalFlowEvaluationService
from trading.research.capital_flow.repository import CapitalFlowRepository
from trading.research.capital_flow.schemas import (
    CapitalFlowAnalyzeRequest,
    CapitalFlowAnalyzeResponse,
    CapitalFlowEvaluation,
    CapitalFlowEvaluationRequest,
    CapitalFlowReadResponse,
)
from trading.research.capital_flow.service import CapitalFlowAnalysisService, _response
from trading.schemas import AnalysisMode


router = APIRouter(
    prefix="/v1/capital-flow",
    tags=["capital-flow-shadow"],
)
repository = CapitalFlowRepository()
service = CapitalFlowAnalysisService(repository=repository)
evaluation_service = CapitalFlowEvaluationService(repository=repository)


@router.post(
    "/analyze",
    response_model=CapitalFlowAnalyzeResponse,
    summary="Run deterministic shadow-only capital-flow analysis",
)
async def analyze_capital_flow(
    request: CapitalFlowAnalyzeRequest,
) -> CapitalFlowAnalyzeResponse:
    try:
        return await service.analyze(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/symbols/{symbol}",
    response_model=CapitalFlowReadResponse,
    summary="Read the latest capital-flow symbol snapshot",
)
def get_capital_flow_symbol(
    symbol: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode | None = Query(default=None),
) -> CapitalFlowReadResponse:
    snapshot = repository.latest_symbol_snapshot(
        symbol=symbol,
        data_cutoff=data_cutoff,
        mode=analysis_mode,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No snapshot for {symbol}")
    return CapitalFlowReadResponse.model_validate(_response(snapshot).model_dump())


@router.get(
    "/sectors/{sector}",
    response_model=CapitalFlowReadResponse,
    summary="Read the latest capital-flow sector snapshot",
)
def get_capital_flow_sector(
    sector: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode | None = Query(default=None),
) -> CapitalFlowReadResponse:
    snapshot = repository.latest_sector_snapshot(
        sector=sector,
        data_cutoff=data_cutoff,
        mode=analysis_mode,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No snapshot for {sector}")
    return CapitalFlowReadResponse.model_validate(_response(snapshot).model_dump())


@router.get(
    "/market",
    response_model=CapitalFlowReadResponse,
    summary="Read the latest partial/full market capital-flow snapshot",
)
def get_capital_flow_market(
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode | None = Query(default=None),
) -> CapitalFlowReadResponse:
    snapshot = repository.latest_market_snapshot(
        data_cutoff=data_cutoff,
        mode=analysis_mode,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No market snapshot")
    return CapitalFlowReadResponse.model_validate(_response(snapshot).model_dump())


@router.post(
    "/evaluate",
    response_model=CapitalFlowEvaluation,
    summary="Record an after-the-fact capital-flow evaluation",
)
def evaluate_capital_flow(
    request: CapitalFlowEvaluationRequest,
) -> CapitalFlowEvaluation:
    try:
        return evaluation_service.evaluate(
            snapshot_id=request.snapshot_id,
            data_cutoff=request.data_cutoff,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
