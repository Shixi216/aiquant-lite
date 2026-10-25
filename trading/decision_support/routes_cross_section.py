"""GET-only formal cross-section endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from trading.decision_support.cross_section_reader import CrossSectionReader
from trading.decision_support.schemas_cross_section import (
    CrossSectionLatestResponse,
    CrossSectionSymbolResponse,
)


router = APIRouter(prefix="/v1/cross-section", tags=["cross-section"])
reader = CrossSectionReader()


@router.get(
    "/latest",
    response_model=CrossSectionLatestResponse,
    summary="Read the latest formal four-dimension cross-section snapshot",
)
def latest_cross_section(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    min_composite: float | None = Query(default=None, ge=-1.0, le=1.0),
    sort: Literal["composite", "tech", "fund", "sent", "margin"] = Query(
        default="composite"
    ),
) -> CrossSectionLatestResponse:
    try:
        return reader.latest(
            limit=limit,
            offset=offset,
            min_composite=min_composite,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/{symbol}",
    response_model=CrossSectionSymbolResponse,
    summary="Read the formal cross-section score for one symbol",
)
def symbol_cross_section(symbol: str) -> CrossSectionSymbolResponse:
    try:
        return reader.for_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
