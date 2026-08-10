from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import logging
import os
from typing import Any

os.environ["HERMES_DB_OWNER_PROCESS"] = "router"
os.environ["HERMES_DB_ENFORCE_OWNER"] = "1"
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config.version import PROJECT_VERSION
from database.db import get_connection, initialize_database
from database.owner_routes import router as database_owner_router
from config.openapi import install_documented_openapi
from router.routes.announcement_pipeline import (
    router as announcement_pipeline_router,
)
from router.routes.news_pipeline import (
    router as news_pipeline_router,
)
from router.registry import get_role, list_roles
from router.schemas.audit import (
    TaskAuditDetail,
    TaskAuditListResponse,
)
from router.services.audit_query import AuditQueryStore
from router.schemas import (
    RouterInvokeRequest,
    RouterInvokeResponse,
)
from router.services import RouterInvocationService
from router.services.routing_policy import routing_policy_catalog
from trading.routes import (
    DEPRECATION_VALUE,
    SUNSET_VALUE,
    router as trading_router,
)
from trading.research.sentiment.routes import (
    router as sentiment_router,
)
from trading.research.policy_news.routes import (
    router as policy_news_router,
)
from trading.research.capital_flow.routes import (
    router as capital_flow_router,
)
from trading.research.orchestration.routes import (
    router as orchestration_router,
)
from trading.research.stock_report_routes import router as stock_report_router
from trading.research.overheat_shadow_routes import router as overheat_shadow_router
from trading.scanner.routes import router as scanner_router
from trading.experiments.routes import router as experiment_router
from data_hub.api.routes.full_market import router as full_market_router
from data_hub.api.routes.stock_basic import router as stock_basic_router
from data_hub.api.routes.realtime_quote import router as realtime_quote_router
from data_hub.api.routes.daily_bars import router as daily_bars_router
from data_hub.api.routes.financial_statements import (
    router as financial_statements_router,
)
from data_hub.api.routes.announcements import router as announcements_router
from data_hub.api.routes.finance_news import router as finance_news_router
from data_hub.api.routes.history import router as history_router
from data_hub.api.routes.market_fact import router as market_fact_router
from router.integration.routes import router as integration_router
from router.integration.schemas import ErrorCode, ResponseStatus, UnifiedResponse
from router.integration.identifiers import new_request_id
from router.integration.model_chain_logging import log_model_chain_stage


SERVICE_NAME = "Hermes OPC Agent Router"
SERVICE_VERSION = PROJECT_VERSION

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="Hermes OPC specialist Agent routing service",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def integration_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    if not request.url.path.startswith("/v1/integration/"):
        return await request_validation_exception_handler(request, exc)
    locations = [
        ".".join(str(part) for part in error.get("loc", ()))
        for error in exc.errors()
    ]
    sanitized = "request validation failed"
    if locations:
        sanitized += ": " + ", ".join(locations[:10])
    payload = UnifiedResponse[dict[str, Any]](
        request_id=new_request_id(),
        status=ResponseStatus.FAILED,
        error_code=ErrorCode.INVALID_REQUEST,
        sanitized_error=sanitized,
        generated_at=datetime.now().astimezone(),
        risk_flags=["REQUEST_VALIDATION_FAILED"],
    )
    return JSONResponse(
        status_code=422,
        content=payload.model_dump(mode="json"),
    )


@app.middleware("http")
async def add_deprecated_route_headers(request: Request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    if getattr(route, "deprecated", False):
        response.headers["Deprecation"] = DEPRECATION_VALUE
        response.headers["Sunset"] = SUNSET_VALUE
    return response


def _check_database_owner() -> tuple[bool, str | None]:
    try:
        with get_connection(read_only=True) as connection:
            result = connection.execute("SELECT 1").fetchone()
        return (result == (1,), None if result == (1,) else "SELECT 1 failed")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "status": "running",
        "health_url": "/health",
        "roles_url": "/v1/roles",
        "invoke_url": "/v1/invoke",
        "routing_policy_url": "/v1/routing/policy",
        "docs_url": "/docs",
    }


@app.get("/health", response_model=None)
async def health() -> dict[str, Any] | JSONResponse:
    database_ok, database_error = _check_database_owner()
    roles = list_roles()

    payload: dict[str, Any] = {
        "status": "ok" if database_ok else "degraded",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "database_owner": {
            "status": "ok" if database_ok else "error",
            "process": "router",
        },
        "registered_roles": len(roles),
        "enabled_roles": sum(1 for role in roles if role.enabled),
    }
    if database_error:
        payload["database_owner"]["error"] = database_error
    if not database_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload

@app.get("/v1/roles")
def roles() -> dict[str, Any]:
    items = [
        role.model_dump()
        for role in list_roles()
    ]

    return {
        "count": len(items),
        "enabled_count": sum(
            1 for item in items if item["enabled"]
        ),
        "roles": items,
    }


@app.get("/v1/routing/policy")
def routing_policy() -> dict[str, object]:
    """Expose the deterministic policy matrix without provider credentials."""
    return routing_policy_catalog()


@app.get("/v1/roles/{role_name}", response_model=None)
def role_detail(
    role_name: str,
) -> dict[str, Any] | JSONResponse:
    role = get_role(role_name)

    if role is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Unknown router role: {role_name}",
            },
        )

    return role.model_dump()


@app.post(
    "/v1/invoke",
    response_model=RouterInvokeResponse,
    summary="调用专业 Agent 角色",
)
async def invoke_role(
    request: RouterInvokeRequest,
) -> RouterInvokeResponse:
    try:
        return await RouterInvocationService().invoke(request)

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Router 调用发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

@app.get(
    "/v1/audit/tasks",
    response_model=TaskAuditListResponse,
    summary="查询最近的 Router 审计任务",
)
def list_audit_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(
        default=None,
        max_length=32,
    ),
    symbol: str | None = Query(
        default=None,
        max_length=32,
    ),
) -> TaskAuditListResponse:
    request_id = new_request_id()
    try:
        result = AuditQueryStore().list_tasks(
            limit=limit,
            status=status,
            symbol=symbol,
        )
    except Exception as exc:
        log_model_chain_stage(
            logger=logging.getLogger("uvicorn.error"),
            request_id=request_id,
            skill_id="model_audit_list",
            provider_id=None,
            provider_registered=None,
            credential_loaded=None,
            request_started=True,
            response_received=False,
            response_status=503,
            audit_task_created=None,
            error_code="AUDIT_DATABASE_UNAVAILABLE",
            sanitized_error=exc,
        )
        raise HTTPException(
            status_code=503,
            detail="模型审计数据库暂时不可用",
        ) from exc
    log_model_chain_stage(
        logger=logging.getLogger("uvicorn.error"),
        request_id=request_id,
        skill_id="model_audit_list",
        provider_id=None,
        provider_registered=None,
        credential_loaded=None,
        request_started=True,
        response_received=True,
        response_status=200,
        audit_task_created=None,
    )
    return result


@app.get(
    "/v1/audit/tasks/{task_id}",
    response_model=TaskAuditDetail,
    summary="查询单个 Router 任务的完整审计链",
)
def get_audit_task(
    task_id: str,
) -> TaskAuditDetail:
    task = AuditQueryStore().get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Audit task not found: {task_id}",
        )

    return task

app.include_router(database_owner_router, include_in_schema=False)
app.include_router(announcement_pipeline_router)
app.include_router(news_pipeline_router)
app.include_router(trading_router)
app.include_router(sentiment_router)
app.include_router(policy_news_router)
app.include_router(capital_flow_router)
app.include_router(orchestration_router)
app.include_router(stock_report_router, include_in_schema=False)
app.include_router(overheat_shadow_router, include_in_schema=False)
app.include_router(scanner_router)
app.include_router(experiment_router)
app.include_router(full_market_router)
app.include_router(stock_basic_router, include_in_schema=False)
app.include_router(realtime_quote_router, include_in_schema=False)
app.include_router(daily_bars_router, include_in_schema=False)
app.include_router(financial_statements_router, include_in_schema=False)
app.include_router(announcements_router, include_in_schema=False)
app.include_router(finance_news_router, include_in_schema=False)
app.include_router(history_router, include_in_schema=False)
app.include_router(market_fact_router, include_in_schema=False)
app.include_router(integration_router)
install_documented_openapi(app)
