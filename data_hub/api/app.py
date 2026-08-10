from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.responses import JSONResponse

from config.version import PROJECT_VERSION
from config.openapi import install_documented_openapi
from data_hub.api.routes.daily_bars import router as daily_bars_router
from data_hub.api.routes.stock_basic import router as stock_basic_router
from data_hub.api.routes.realtime_quote import router as realtime_quote_router
from data_hub.api.routes.financial_statements import (
    router as financial_statements_router,
)
from data_hub.api.routes.announcements import router as announcements_router
from data_hub.api.routes.finance_news import router as finance_news_router
from data_hub.api.routes.full_market import router as full_market_router
from data_hub.api.routes.history import router as history_router
from database.db import get_connection, initialize_database


SERVICE_NAME = "Hermes OPC Data Hub"
SERVICE_VERSION = PROJECT_VERSION
ROUTER_URL = "http://127.0.0.1:8765"

CAPABILITIES = [
    {
        "name": "get_stock_basic",
        "description": "获取并核验A股基础信息",
    },
    {
        "name": "get_daily_bars",
        "description": "获取并交叉核验A股日线行情",
    },
    {
        "name": "get_realtime_quote",
        "description": "获取实时报价或返回已核验日线降级结果",
    },
    {
        "name": "get_financial_statement",
        "description": "获取同一报告期的三大财务报表",
    },
    {
        "name": "get_announcements",
        "description": "获取巨潮资讯官方上市公司公告",
    },
    {
        "name": "get_finance_news",
        "description": "获取个股财经媒体新闻",
    },
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    if (
        os.environ.get("HERMES_DB_OWNER_PROCESS") == "router"
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        initialize_database()
    yield


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="Hermes OPC 本地结构化金融数据服务",
    lifespan=lifespan,
)
@app.middleware("http")
async def proxy_database_requests(request: Request, call_next):
    if (
        os.environ.get("HERMES_DB_OWNER_PROCESS") == "router"
        or "PYTEST_CURRENT_TEST" in os.environ
        or request.url.path in {"/", "/health", "/v1/capabilities"}
    ):
        return await call_next(request)
    try:
        async with httpx.AsyncClient(
            base_url=ROUTER_URL,
            timeout=120,
            trust_env=False,
        ) as client:
            response = await client.request(
                request.method,
                request.url.path,
                params=request.query_params,
                content=await request.body(),
                headers={
                    key: value
                    for key, value in request.headers.items()
                    if key.casefold() not in {"host", "content-length"}
                },
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "数据库所有者Router暂时不可用",
                "error_type": type(exc).__name__,
            },
        )


def _check_database() -> tuple[bool, str | None]:
    try:
        if os.environ.get("HERMES_DB_OWNER_PROCESS") != "router":
            response = httpx.get(f"{ROUTER_URL}/health", timeout=3)
            response.raise_for_status()
            payload = response.json()
            ok = payload.get("database_owner", {}).get("status") == "ok"
            return ok, None if ok else "Router database owner is unhealthy"
        with get_connection(read_only=True) as connection:
            result = connection.execute("SELECT 1").fetchone()
        return result == (1,), None if result == (1,) else "DuckDB SELECT 1 failed"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "status": "running",
        "health_url": "/health",
        "docs_url": "/docs",
    }


@app.get("/health", response_model=None)
def health() -> dict[str, Any] | JSONResponse:
    database_ok, database_error = _check_database()

    payload: dict[str, Any] = {
        "status": "ok" if database_ok else "degraded",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "database": {
            "status": "ok" if database_ok else "error",
            "engine": "DuckDB",
        },
    }

    if database_error:
        payload["database"]["error"] = database_error

    if not database_ok:
        return JSONResponse(
            status_code=503,
            content=payload,
        )

    return payload


@app.get("/v1/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "count": len(CAPABILITIES),
        "capabilities": CAPABILITIES,
    }


@app.post("/v1/scan")
def unified_scan(payload: dict[str, Any]) -> dict[str, Any]:
    """统一扫描入口（Web/企微/QQ 共用统一链路）

    请求: {"query": "今天有什么好票", "channel": "wecom", "local_user_id": "..."}
    响应: query_intent / realtime_snapshot_id / action / candidates / research_results
    统一走 QueryRouter → IntradayScanner/EodScanner → Research → Decision
    """
    from trading.scanner.unified_gateway import ScanRequest, UnifiedScanGateway

    req = ScanRequest(
        query=str(payload.get("query", "")),
        local_user_id=str(payload.get("local_user_id", "u_wecom_ZhangTianYi")),
        external_user_id=str(payload.get("external_user_id", "ZhangTianYi")),
        channel=str(payload.get("channel", "api")),
        max_research=int(payload.get("max_research", 15)),
        max_candidates=int(payload.get("max_candidates", 50)),
    )
    resp = UnifiedScanGateway().scan(req)
    return {
        "query": resp.query,
        "query_intent": resp.query_intent,
        "market_session": resp.market_session,
        "market_session_zh": resp.market_session_zh,
        "latest_completed_trade_date": resp.latest_completed_trade_date,
        "realtime_snapshot_id": resp.realtime_snapshot_id,
        "daily_snapshot_id": resp.daily_snapshot_id,
        "formal_score": resp.formal_score,
        "veto_result": resp.veto_result,
        "action": resp.action,
        "data_cutoff": resp.data_cutoff,
        "candidates": resp.candidates,
        "research_results": resp.research_results,
        "elapsed_seconds": resp.elapsed_seconds,
        "channel": resp.channel,
        "errors": resp.errors,
    }

app.include_router(stock_basic_router)
app.include_router(daily_bars_router)
app.include_router(realtime_quote_router)
app.include_router(financial_statements_router)
app.include_router(announcements_router)
app.include_router(finance_news_router)
app.include_router(full_market_router)
app.include_router(history_router)
install_documented_openapi(app)
