from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config.version import PROJECT_VERSION


SERVICE_NAME = "Hermes OPC Data Hub"
ROUTER_URL = "http://127.0.0.1:8765"

app = FastAPI(
    title=f"{SERVICE_NAME} compatibility proxy",
    version=PROJECT_VERSION,
    description="Database-free compatibility proxy to the Router owner process.",
)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": PROJECT_VERSION,
        "status": "running",
        "database_owner": "router",
        "proxy_target": ROUTER_URL,
    }


@app.get("/health", response_model=None)
def health() -> dict[str, Any] | JSONResponse:
    try:
        response = httpx.get(f"{ROUTER_URL}/health", timeout=3, trust_env=False)
        response.raise_for_status()
        owner = response.json()
        ok = owner.get("database_owner", {}).get("status") == "ok"
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "service": SERVICE_NAME,
                "version": PROJECT_VERSION,
                "database_owner": "router",
                "error": type(exc).__name__,
            },
        )
    return {
        "status": "ok" if ok else "degraded",
        "service": SERVICE_NAME,
        "version": PROJECT_VERSION,
        "database_owner": "router",
        "router_pid_owned": ok,
    }


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    try:
        async with httpx.AsyncClient(
            base_url=ROUTER_URL,
            timeout=180,
            trust_env=False,
        ) as client:
            upstream = await client.request(
                request.method,
                f"/{path}",
                params=request.query_params,
                content=await request.body(),
                headers={
                    key: value
                    for key, value in request.headers.items()
                    if key.casefold() not in {"host", "content-length"}
                },
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "数据库所有者Router暂时不可用",
                "error_type": type(exc).__name__,
            },
        )


__all__ = ["app"]
