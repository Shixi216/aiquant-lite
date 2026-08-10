from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from trading.research.sentiment.evaluation import (
    SentimentEvaluationService,
)
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import (
    SentimentAnalyzeRequest,
    SentimentAnalyzeResponse,
    SentimentEvaluation,
    SentimentEvaluationRequest,
    SentimentReadResponse,
)
from trading.research.sentiment.service import SentimentAnalysisService
from trading.schemas import AnalysisMode


router = APIRouter(prefix="/v1/sentiment", tags=["sentiment-shadow"])
repository = SentimentRepository()
service = SentimentAnalysisService(repository=repository)
evaluation_service = SentimentEvaluationService(repository=repository)


@router.post(
    "/analyze",
    response_model=SentimentAnalyzeResponse,
    summary="Run shadow-only sentiment analysis",
)
async def analyze_sentiment(
    request: SentimentAnalyzeRequest,
) -> SentimentAnalyzeResponse:
    try:
        return await service.analyze(request)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/symbols/{symbol}",
    response_model=SentimentReadResponse,
    summary="Read the latest persisted symbol sentiment snapshot",
)
def get_symbol_sentiment(
    symbol: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.SCREENING),
) -> SentimentReadResponse:
    snapshot = repository.latest_symbol_snapshot(
        symbol=symbol,
        data_cutoff=data_cutoff,
        mode=(
            None
            if analysis_mode == AnalysisMode.SCREENING
            else analysis_mode
        ),
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sentiment snapshot for {symbol} at cutoff",
        )
    return SentimentReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=snapshot,
        missing_fields=snapshot.missing_fields,
        risk_flags=snapshot.risk_flags,
        evidence_ids=snapshot.evidence_ids,
    )


@router.get(
    "/market",
    response_model=SentimentReadResponse,
    summary="Read the latest persisted market breadth snapshot",
)
def get_market_sentiment(
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.SCREENING),
) -> SentimentReadResponse:
    snapshot = repository.latest_market_snapshot(
        data_cutoff=data_cutoff,
        mode=(
            None
            if analysis_mode == AnalysisMode.SCREENING
            else analysis_mode
        ),
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No market sentiment snapshot at cutoff",
        )
    return SentimentReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=snapshot,
        missing_fields=snapshot.missing_fields,
        risk_flags=snapshot.risk_flags,
        evidence_ids=snapshot.evidence_ids,
    )


@router.get(
    "/events/{event_cluster_id}",
    response_model=SentimentReadResponse,
    summary="Read the latest persisted sentiment analysis for an event",
)
def get_event_sentiment(
    event_cluster_id: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.RESEARCH),
) -> SentimentReadResponse:
    analysis = repository.latest_event_analysis(
        event_cluster_id=event_cluster_id,
        data_cutoff=data_cutoff,
    )
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sentiment analysis for {event_cluster_id}",
        )
    return SentimentReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=analysis,
        risk_flags=analysis.risk_flags,
        evidence_ids=analysis.evidence_ids,
    )


@router.post(
    "/evaluate",
    response_model=SentimentEvaluation,
    summary="Record an after-the-fact sentiment evaluation",
)
def evaluate_sentiment(
    request: SentimentEvaluationRequest,
) -> SentimentEvaluation:
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
