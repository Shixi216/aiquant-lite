from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    DecisionShadowResponse,
    EvaluationRequest,
    EvaluationResponse,
    ResearchRequest,
    ResearchResponse,
    ScreeningRequest,
    ScreeningResponse,
    SymbolOrchestrationResponse,
)
from trading.research.orchestration.service import OrchestrationService
from trading.schemas import AnalysisMode


router = APIRouter(
    prefix="/v1/orchestration",
    tags=["five-factor-orchestration"],
)
service = OrchestrationService()


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/screen", response_model=ScreeningResponse)
def screen(request: ScreeningRequest) -> ScreeningResponse:
    try:
        return service.screen(request)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest) -> ResearchResponse:
    try:
        return service.research(request)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/decision-shadow",
    response_model=DecisionShadowResponse,
)
def decision_shadow(
    request: DecisionShadowRequest,
) -> DecisionShadowResponse:
    try:
        return service.decision_shadow(request)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/symbols/{symbol}",
    response_model=SymbolOrchestrationResponse,
)
def symbol(
    symbol: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.SCREENING),
) -> SymbolOrchestrationResponse:
    try:
        return service.symbol(
            symbol=symbol,
            data_cutoff=data_cutoff,
            analysis_mode=analysis_mode,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.post("/evaluate", response_model=EvaluationResponse)
def evaluate(request: EvaluationRequest) -> EvaluationResponse:
    try:
        return service.evaluate(request)
    except ValueError as exc:
        raise _http_error(exc) from exc


__all__ = ["router", "service"]
