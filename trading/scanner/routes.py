from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from trading.scanner.schemas import (
    ScannerEvaluationRequest,
    ScannerEvaluationResponse,
    ScannerParseRequest,
    ScannerParseResponse,
    ScannerScanRequest,
    ScannerScanResponse,
)
from trading.scanner.service import MarketScannerService


router = APIRouter(prefix="/v1/scanner", tags=["market-scanner"])
service = MarketScannerService()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        status = 404
    elif isinstance(exc, RuntimeError) and str(exc) == "SCANNER_EMPTY_UNIVERSE":
        status = 409
    else:
        status = 422
    return HTTPException(status_code=status, detail=str(exc))


@router.post("/parse", response_model=ScannerParseResponse)
def parse_query(request: ScannerParseRequest) -> ScannerParseResponse:
    try:
        return service.parse(request)
    except ValueError as exc:
        raise _error(exc) from exc


@router.post(
    "/scan",
    response_model=ScannerScanResponse | ScannerParseResponse,
)
def scan(
    request: ScannerScanRequest,
) -> ScannerScanResponse | ScannerParseResponse:
    try:
        return service.scan(request)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}", response_model=dict[str, Any])
def run_detail(run_id: str) -> dict[str, Any]:
    value = service.run_detail(run_id)
    if value is None:
        raise HTTPException(status_code=404, detail="scanner run not found")
    return value


@router.get("/runs/{run_id}/candidates", response_model=dict[str, Any])
def run_candidates(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "candidates": service.run_candidates(run_id),
        "is_trade_recommendation": False,
    }


@router.get("/symbols/{symbol}", response_model=dict[str, Any])
def symbol(symbol: str) -> dict[str, Any]:
    value = service.symbol(symbol)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail="persisted scanner candidate not found",
        )
    return value


@router.post("/evaluate", response_model=ScannerEvaluationResponse)
def evaluate(
    request: ScannerEvaluationRequest,
) -> ScannerEvaluationResponse:
    try:
        return service.evaluate(request)
    except (KeyError, ValueError) as exc:
        raise _error(exc) from exc


__all__ = ["router", "service"]
