"""GET-only server-side market breadth endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.market_breadth import MarketBreadthResponse
from data_hub.services.market_breadth_service import MarketBreadthService


router = APIRouter(tags=["market-breadth"])
service = MarketBreadthService()


@router.get(
    "/v1/market-snapshots/latest/breadth",
    response_model=MarketBreadthResponse,
    summary="Aggregate market breadth over ALL items of the latest snapshot",
)
def latest_market_breadth(
    maximum_age_seconds: int = Query(default=300, ge=0, le=604_800),
) -> MarketBreadthResponse:
    try:
        return service.latest(maximum_age_seconds=maximum_age_seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
