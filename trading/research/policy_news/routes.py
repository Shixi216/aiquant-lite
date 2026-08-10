from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from trading.research.policy_news.evaluation import (
    PolicyNewsEvaluationService,
)
from trading.research.policy_news.repository import PolicyNewsRepository
from trading.research.policy_news.schemas import (
    PolicyNewsAnalyzeRequest,
    PolicyNewsAnalyzeResponse,
    PolicyNewsEvaluation,
    PolicyNewsEvaluationRequest,
    PolicyNewsReadResponse,
)
from trading.research.policy_news.service import PolicyNewsAnalysisService
from trading.schemas import AnalysisMode


router = APIRouter(
    prefix="/v1/policy-news",
    tags=["policy-news-shadow"],
)
repository = PolicyNewsRepository()
service = PolicyNewsAnalysisService(repository=repository)
evaluation_service = PolicyNewsEvaluationService(repository=repository)


@router.post(
    "/analyze",
    response_model=PolicyNewsAnalyzeResponse,
    summary="Run shadow-only policy and material-news analysis",
)
async def analyze_policy_news(
    request: PolicyNewsAnalyzeRequest,
) -> PolicyNewsAnalyzeResponse:
    try:
        return await service.analyze(request)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/symbols/{symbol}",
    response_model=PolicyNewsReadResponse,
    summary="Read the latest policy/news symbol snapshot",
)
def get_symbol_policy_news(
    symbol: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.SCREENING),
) -> PolicyNewsReadResponse:
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
            detail=f"No policy/news snapshot for {symbol} at cutoff",
        )
    return PolicyNewsReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=snapshot,
        missing_fields=snapshot.missing_fields,
        risk_flags=snapshot.risk_flags,
        evidence_ids=snapshot.evidence_ids,
        shared_sentiment_event_ids=(
            snapshot.shared_sentiment_event_ids
        ),
    )


@router.get(
    "/sectors/{sector}",
    response_model=PolicyNewsReadResponse,
    summary="Read the latest policy/news sector snapshot",
)
def get_sector_policy_news(
    sector: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.SCREENING),
) -> PolicyNewsReadResponse:
    snapshot = repository.latest_sector_snapshot(
        sector=sector,
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
            detail=f"No policy/news snapshot for sector {sector} at cutoff",
        )
    return PolicyNewsReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=snapshot,
        missing_fields=snapshot.missing_fields,
        risk_flags=snapshot.risk_flags,
        evidence_ids=snapshot.evidence_ids,
        shared_sentiment_event_ids=(
            snapshot.shared_sentiment_event_ids
        ),
    )


@router.get(
    "/events/{event_cluster_id}",
    response_model=PolicyNewsReadResponse,
    summary="Read the latest policy/news analysis for a shared event",
)
def get_event_policy_news(
    event_cluster_id: str,
    data_cutoff: datetime = Query(),
    analysis_mode: AnalysisMode = Query(default=AnalysisMode.RESEARCH),
) -> PolicyNewsReadResponse:
    analysis = repository.latest_event_analysis(
        event_cluster_id=event_cluster_id,
        data_cutoff=data_cutoff,
    )
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail=f"No policy/news analysis for {event_cluster_id}",
        )
    return PolicyNewsReadResponse(
        analysis_mode=analysis_mode,
        data_cutoff=data_cutoff,
        result=analysis,
        risk_flags=analysis.risk_flags,
        evidence_ids=analysis.evidence_ids,
        implementation_status=[analysis.implementation_status],
        text_completeness=[analysis.text_completeness],
        shared_sentiment_event_ids=(
            [analysis.event_cluster_id]
            if analysis.shared_sentiment_analysis_ids
            else []
        ),
    )


@router.post(
    "/evaluate",
    response_model=PolicyNewsEvaluation,
    summary="Record an after-the-fact policy/news evaluation",
)
def evaluate_policy_news(
    request: PolicyNewsEvaluationRequest,
) -> PolicyNewsEvaluation:
    try:
        return evaluation_service.evaluate(
            snapshot_id=request.snapshot_id,
            data_cutoff=request.data_cutoff,
            actually_implemented=request.actually_implemented,
            terminated_or_retracted=request.terminated_or_retracted,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
