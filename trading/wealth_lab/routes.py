"""Wealth Lab HTTP 路由（/v1/wealth-lab/*，契约 v1）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from trading.wealth_lab.schemas import (
    BacktestRequest,
    StressTestRequest,
    envelope,
)
from trading.wealth_lab.service import WealthLabService

router = APIRouter(prefix="/v1/wealth-lab", tags=["wealth-lab"])
service = WealthLabService()


@router.get("/meta")
def wealth_lab_meta():
    return envelope(service.meta())


@router.get("/assets")
def wealth_lab_assets():
    return envelope(service.assets())


@router.post("/backtests/run")
def wealth_lab_run_backtest(request: BacktestRequest):
    try:
        data = service.run(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if data.get("data_mode") == "UNAVAILABLE":
        return envelope(
            data, status="FAILED", error_code="DATA_UNAVAILABLE",
            sanitized_error=f"数据不可用：{data.get('reason', '')}",
        )
    return envelope(data)


@router.get("/backtests/{backtest_id}")
def wealth_lab_get_backtest(backtest_id: str):
    record = service.get_backtest(backtest_id)
    if record is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return envelope(record["result"])


@router.get("/backtests/{backtest_id}/risk")
def wealth_lab_risk(backtest_id: str):
    data = service.risk(backtest_id)
    if data is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return envelope(data)


@router.post("/stress-tests")
def wealth_lab_stress(request: StressTestRequest):
    try:
        data = service.stress(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if data.get("data_mode") == "UNAVAILABLE":
        return envelope(
            data, status="FAILED", error_code="DATA_UNAVAILABLE",
            sanitized_error=f"数据不可用：{data.get('reason', '')}",
        )
    return envelope(data)
