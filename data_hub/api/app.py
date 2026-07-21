from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from data_hub.api.routes.daily_bars import router as daily_bars_router
from data_hub.api.routes.stock_basic import router as stock_basic_router
from data_hub.api.routes.realtime_quote import router as realtime_quote_router
from data_hub.api.routes.financial_statements import (
    router as financial_statements_router,
)
from data_hub.api.routes.announcements import router as announcements_router
from data_hub.api.routes.finance_news import router as finance_news_router
from database.db import get_connection, initialize_database


SERVICE_NAME = "Hermes OPC Data Hub"
SERVICE_VERSION = "0.1.0"

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
    initialize_database()
    yield


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="Hermes OPC 本地结构化金融数据服务",
    lifespan=lifespan,
)


def _check_database() -> tuple[bool, str | None]:
    try:
        with get_connection() as connection:
            result = connection.execute(
                "SELECT 1"
            ).fetchone()

        if result != (1,):
            return False, "DuckDB SELECT 1 返回结果异常"

        return True, None

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

app.include_router(stock_basic_router)
app.include_router(daily_bars_router)
app.include_router(realtime_quote_router)
app.include_router(financial_statements_router)
app.include_router(announcements_router)
app.include_router(finance_news_router)
