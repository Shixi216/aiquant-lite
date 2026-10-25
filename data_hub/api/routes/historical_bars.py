"""GET-only historical daily-bars endpoint (read-only)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.historical_bars import (
    AdjustmentType,
    HistoricalBarsResponse,
)
from data_hub.services.historical_bars_service import HistoricalBarsService


router = APIRouter(tags=["historical-bars"])
service = HistoricalBarsService()


@router.get(
    "/v1/historical-bars/{symbol}",
    response_model=HistoricalBarsResponse,
    summary="Read the most recent deduplicated daily bars for one symbol",
)
def historical_bars(
    symbol: str,
    days: int = Query(default=60, ge=5, le=250),
    end_date: date | None = Query(default=None),
    adjustment_type: AdjustmentType = Query(default="FORWARD_ADJUSTED"),
) -> HistoricalBarsResponse:
    if adjustment_type not in {"FORWARD_ADJUSTED", "RAW"}:
        raise HTTPException(
            status_code=422,
            detail=(
                "adjustment_type must be FORWARD_ADJUSTED or RAW; "
                "BACKWARD_ADJUSTED is not derivable from RAW + factors"
            ),
        )
    try:
        return service.latest(
            symbol=symbol,
            days=days,
            end_date=end_date,
            adjustment_type=adjustment_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
