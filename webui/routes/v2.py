"""GET-only /webui/v2/* endpoints.

All blocks degrade independently; the WebUI never computes formal scores.
Dev fixtures (contract samples) are env-gated and injected BEFORE the live
call path so tests can lock response shapes.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from webui.config import FIXTURES_DIR, FIXTURE_MODE
from webui.providers.bar_chain import build_chart
from webui.providers.symbol import normalize_symbol
from webui.services import (
    dashboard_service,
    opportunity_service,
    stock_intel_service,
)

router = APIRouter(prefix="/webui/v2", tags=["webui-v2"])


def _fixture(name: str) -> dict | None:
    if not FIXTURE_MODE:
        return None
    path = FIXTURES_DIR / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/dashboard")
async def dashboard(request: Request) -> JSONResponse:
    sample = _fixture("dashboard.json")
    if sample is not None:
        sample["fixture_mode"] = True
        return JSONResponse(sample)
    payload = await dashboard_service.dashboard(request.app.state.router_client)
    payload["fixture_mode"] = False
    return JSONResponse(payload)


@router.get("/opportunities")
async def opportunities(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    min_score: float | None = None,
    sort: str = "composite",
) -> JSONResponse:
    sample = _fixture("opportunities.json")
    if sample is not None:
        sample["fixture_mode"] = True
        return JSONResponse(sample)
    payload = await opportunity_service.opportunities(
        request.app.state.router_client,
        limit=limit,
        offset=offset,
        min_score=min_score,
        sort=sort,
    )
    payload = await opportunity_service.attach_quotes(
        request.app.state.router_client, payload
    )
    payload["fixture_mode"] = False
    return JSONResponse(payload)


@router.get("/stocks/{symbol}")
async def stocks(request: Request, symbol: str) -> JSONResponse:
    if normalize_symbol(symbol) is None:
        return JSONResponse(status_code=422, content={"detail": "invalid symbol"})
    sample = _fixture(f"stock_{symbol}.json")
    if sample is not None:
        sample["fixture_mode"] = True
        return JSONResponse(sample)
    payload = await stock_intel_service.stock_intel(
        request.app.state.router_client, symbol
    )
    payload["fixture_mode"] = False
    return JSONResponse(payload)


@router.get("/stocks/{symbol}/chart")
async def chart(
    request: Request,
    symbol: str,
    days: int = 60,
    adjustment_type: str = "FORWARD_ADJUSTED",
) -> JSONResponse:
    if normalize_symbol(symbol) is None:
        return JSONResponse(status_code=422, content={"detail": "invalid symbol"})
    if days < 5 or days > 250 or adjustment_type not in {"FORWARD_ADJUSTED", "RAW"}:
        return JSONResponse(
            status_code=422,
            content={"detail": "days must be 5-250; adjustment_type must be FORWARD_ADJUSTED or RAW"},
        )
    sample = _fixture(f"chart_{symbol}.json")
    if sample is not None:
        sample["fixture_mode"] = True
        return JSONResponse(sample)
    try:
        payload_model = await build_chart(
            symbol,
            days=days,
            router_client=request.app.state.router_client,
            adjustment_type=adjustment_type,
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    payload = {
        "symbol": payload_model.symbol,
        "bars": payload_model.bars,
        "data_source": payload_model.data_source,
        "formal_status": payload_model.formal_status,
        "data_time": payload_model.data_time,
        "market_coverage_date": payload_model.market_coverage_date,
        "adjustment": payload_model.adjustment,
        "tail_fill": payload_model.tail_fill,
        "unavailable_reason": payload_model.unavailable_reason,
    }
    payload["fixture_mode"] = False
    return JSONResponse(payload)


__all__ = ["router"]
