from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from data_hub.schemas.service import MarketFactVerificationResponse
from data_hub.services.market_fact_service import MarketFactService


router = APIRouter(prefix="/v1/market-facts", tags=["market-facts"])


class MarketFactVerifyRequest(BaseModel):
    symbol: str
    data_type: Literal[
        "stock_basic",
        "realtime_quote",
        "daily_bar",
        "financial_statement",
        "announcement",
        "finance_news",
    ]
    field: str
    expected_value: str | int | float | bool
    event_date: str | None = None
    tolerance: float = Field(default=0.005, ge=0)


@router.post("/verify", response_model=MarketFactVerificationResponse)
def verify_market_fact(
    request: MarketFactVerifyRequest,
) -> MarketFactVerificationResponse:
    try:
        return MarketFactService().verify_market_fact(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["MarketFactVerifyRequest", "router"]
