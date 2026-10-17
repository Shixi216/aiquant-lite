from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from webui.artifacts.sim_daily import (
    ArtifactNotFound,
    list_dates,
    load_day_inventory,
    load_symbol_series,
    load_timepoint,
)

router = APIRouter(prefix="/webui/sim-daily", tags=["webui-sim-daily"])


def _or_404(loader: Any, *args: Any) -> Any:
    try:
        return loader(*args)
    except ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact file missing") from exc


@router.get("/dates")
def get_dates() -> dict[str, Any]:
    return list_dates()


@router.get("/dates/{date}")
def get_day_inventory(date: str) -> dict[str, Any]:
    return _or_404(load_day_inventory, date)


@router.get("/dates/{date}/timepoints/{timepoint}")
def get_timepoint_snapshot(date: str, timepoint: str) -> dict[str, Any]:
    return _or_404(load_timepoint, date, timepoint)


@router.get("/dates/{date}/symbols/{symbol}")
def get_symbol_series(date: str, symbol: str) -> dict[str, Any]:
    return _or_404(load_symbol_series, date, symbol)
