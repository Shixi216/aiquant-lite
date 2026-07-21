from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

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
from trading.routes import router as trading_router


SERVICE_NAME = "Hermes OPC Agent Router"
SERVICE_VERSION = "0.8.0"
DATA_HUB_URL = "http://127.0.0.1:8766"


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="Hermes OPC specialist Agent routing service",
)


async def _check_data_hub() -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(
            base_url=DATA_HUB_URL,
            timeout=3,
            trust_env=False,
        ) as client:
            response = await client.get("/health")
            response.raise_for_status()
            payload = response.json()

        if payload.get("status") != "ok":
            return False, f"unexpected status: {payload}"

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
        "roles_url": "/v1/roles",
        "invoke_url": "/v1/invoke",
        "routing_policy_url": "/v1/routing/policy",
        "docs_url": "/docs",
    }


@app.get("/health", response_model=None)
async def health() -> dict[str, Any] | JSONResponse:
    data_hub_ok, data_hub_error = await _check_data_hub()
    roles = list_roles()

    payload: dict[str, Any] = {
        "status": "ok" if data_hub_ok else "degraded",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "data_hub": {
            "status": "ok" if data_hub_ok else "error",
            "url": DATA_HUB_URL,
        },
        "registered_roles": len(roles),
        "enabled_roles": sum(
            1 for role in roles if role.enabled
        ),
    }

    if data_hub_error:
        payload["data_hub"]["error"] = data_hub_error

    if not data_hub_ok:
        return JSONResponse(
            status_code=503,
            content=payload,
        )

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
    return AuditQueryStore().list_tasks(
        limit=limit,
        status=status,
        symbol=symbol,
    )


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

app.include_router(announcement_pipeline_router)
app.include_router(news_pipeline_router)
app.include_router(trading_router)
