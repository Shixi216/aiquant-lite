from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    AdjustmentType,
    HistoryBackfillRequest,
    HistoryBackfillResponse,
    HistoryCoverageResponse,
    HistoryRunActionRequest,
    HistoryRunDetail,
)
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_coverage_service import HistoryCoverageService


router = APIRouter(tags=["historical-market-data"])
repository = HistoryRepository()
backfill_service = HistoryBackfillService(repository=repository)
coverage_service = HistoryCoverageService(repository=repository)


@router.post(
    "/v1/history-backfill/runs",
    response_model=HistoryBackfillResponse,
    summary="Plan or explicitly apply a bounded historical-bar backfill",
)
def create_history_backfill(
    request: HistoryBackfillRequest,
) -> HistoryBackfillResponse:
    try:
        return backfill_service.run(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/v1/history-backfill/runs/{run_id}",
    response_model=HistoryRunDetail,
    summary="Read one historical backfill run and its request audits",
)
def get_history_backfill(run_id: str) -> HistoryRunDetail:
    try:
        return backfill_service.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="history run not found") from exc


@router.post(
    "/v1/history-backfill/runs/{run_id}/resume",
    response_model=HistoryBackfillResponse,
    summary="Dry-run or explicitly resume a bounded historical backfill",
)
def resume_history_backfill(
    run_id: str,
    request: HistoryRunActionRequest,
) -> HistoryBackfillResponse:
    try:
        return backfill_service.resume(run_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="history run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/v1/history-backfill/runs/{run_id}/cancel",
    response_model=HistoryRunDetail,
    summary="Dry-run or explicitly cancel a historical backfill",
)
def cancel_history_backfill(
    run_id: str,
    request: HistoryRunActionRequest,
) -> HistoryRunDetail:
    try:
        return backfill_service.cancel(run_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="history run not found") from exc


@router.get(
    "/v1/data-coverage/history",
    response_model=HistoryCoverageResponse,
    summary="Calculate trading-calendar-based 20/60-day history coverage",
)
def get_history_coverage(
    data_cutoff: datetime = Query(),
    as_of_trade_date: date | None = Query(default=None),
    adjustment_type: AdjustmentType = Query(default=AdjustmentType.RAW),
) -> HistoryCoverageResponse:
    try:
        return coverage_service.generate(
            data_cutoff=data_cutoff,
            adjustment_type=adjustment_type,
            as_of_trade_date=as_of_trade_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
