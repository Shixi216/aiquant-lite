from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from trading.research.stock_report_service import StockReportService


router = APIRouter(prefix="/v1/research", tags=["stock-report"])


class StockReportRequest(BaseModel):
    symbol: str
    days: int = Field(default=90, ge=1, le=1000)
    no_fetch: bool = False
    no_ai: bool = False
    stale_ok: bool = False


@router.post("/stock-report")
def stock_report(request: StockReportRequest) -> dict[str, object]:
    try:
        report = StockReportService().run(
            **request.model_dump(),
            request_id=f"stock-report-{uuid.uuid4().hex}",
        )
        return {
            "report": report,
            "database_owner": "router",
            "order_created": False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["StockReportRequest", "router"]
