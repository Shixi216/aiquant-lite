from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config.version import PROJECT_VERSION
from fastapi.middleware.cors import CORSMiddleware
from webui import proxy
from webui.config import FRONTEND_DIST, ROUTER_URL, SERVICE_NAME
from webui.routes import capital_behavior, research, sim_daily
from webui.routes import v2 as v2_routes
from webui.routes import quant as quant_routes

_RESERVED_PREFIXES = ("api/", "webui/", "assets/", "docs", "redoc", "openapi.json", "favicon.ico")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.router_client = httpx.AsyncClient(
        base_url=ROUTER_URL,
        timeout=httpx.Timeout(30.0, connect=5.0),
        trust_env=False,
    )
    try:
        yield
    finally:
        await app.state.router_client.aclose()


app = FastAPI(
    title="怡怡投研",
    version=PROJECT_VERSION,
    description=(
        "只读可视化服务：文件型研究产物直接读取，API 数据经 GET-only 代理转发到 Router(8765)。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    client: httpx.AsyncClient = app.state.router_client
    router_status = "unreachable"
    try:
        response = await client.get("/health", timeout=3)
        router_status = "ok" if response.status_code == 200 else f"http_{response.status_code}"
    except Exception:
        router_status = "unreachable"
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": PROJECT_VERSION,
        "router_status": router_status,
        "dist_built": (FRONTEND_DIST / "index.html").is_file(),
    }


app.include_router(sim_daily.router)
app.include_router(capital_behavior.router)
app.include_router(research.router)
app.include_router(v2_routes.router)
app.include_router(quant_routes.router)
app.include_router(proxy.router, prefix="/api")

_DIST_INDEX = FRONTEND_DIST / "index.html"
if _DIST_INDEX.is_file():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        if path.startswith(_RESERVED_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return FileResponse(_DIST_INDEX, headers={"Cache-Control": "no-cache"})
else:

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_missing(path: str):
        if path.startswith(_RESERVED_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "前端尚未构建。执行：npm --prefix frontend ci && "
                    "npm --prefix frontend run build"
                )
            },
        )


__all__ = ["app"]
