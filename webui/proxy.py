"""GET-only passthrough proxy to the Router owner process.

Restricting the proxy to GET keeps the Web UI read-only: mutating Router
endpoints (decision creation, kill-switch, scans, syncs) are unreachable
through :8767 by construction.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter(tags=["webui-proxy"])

_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}


@router.api_route("/{path:path}", methods=["GET"])
async def proxy_get(path: str, request: Request) -> Response:
    client: httpx.AsyncClient = request.app.state.router_client
    headers = {
        key: value for key, value in request.headers.items() if key.casefold() not in _SKIP_HEADERS
    }
    try:
        upstream = await client.get(f"/{path}", params=request.query_params, headers=headers)
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Router(8765)未运行或不可达", "error_type": "ConnectError"},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"detail": "Router(8765)响应超时", "error_type": "Timeout"},
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
