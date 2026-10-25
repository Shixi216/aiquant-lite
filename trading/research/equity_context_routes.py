"""GET-only endpoints for database-backed equity research context."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, HTTPException, Query

from trading.research.equity_context import EquityContextReader

router = APIRouter(prefix="/v1/equity-context", tags=["equity-context"])
reader = EquityContextReader()


@router.get("/overview", summary="Read market, macro and sector context")
def overview(sector_limit: int = Query(default=100, ge=1, le=100)) -> dict:
    try:
        return reader.overview(sector_limit=sector_limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (duckdb.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/search", summary="Search persisted stocks by code or Chinese name")
def search(query: str = Query(min_length=1, max_length=80), limit: int = Query(default=8, ge=1, le=20)) -> dict:
    try:
        items = reader.search_stocks(query, limit=limit)
        return {"query": query, "items": items, "total": len(items)}
    except (duckdb.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/dragon-tiger", summary="Read latest persisted post-close Dragon-Tiger events")
def dragon_tiger(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    try:
        return reader.dragon_tiger_board(limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (duckdb.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/stocks/{symbol}", summary="Read detailed persisted stock context")
def stock(symbol: str) -> dict:
    try:
        return reader.stock(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (duckdb.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
