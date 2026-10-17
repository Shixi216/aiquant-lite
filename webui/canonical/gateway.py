"""Unified read-only gateway to the Router canonical plane.

Every call returns a RouterResult and never raises. A 404 on one of the
endpoints added in this round is reported as ``endpoint_not_deployed`` so the
UI can say "等待 Router 重启" instead of "暂无数据".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

NEW_ENDPOINT_PATHS = (
    "/v1/historical-bars",
    "/v1/market-snapshots/latest/breadth",
    "/v1/cross-section",
    "/v1/decision-packets/v2",
    "/v1/scanner/runs",
)

ROUTER_UNREACHABLE = "router_unreachable"
UPSTREAM_TIMEOUT = "upstream_timeout"
ENDPOINT_NOT_DEPLOYED = "endpoint_not_deployed"
NOT_FOUND = "not_found"
UPSTREAM_ERROR = "upstream_error"


@dataclass(frozen=True)
class RouterResult:
    ok: bool
    status: int | None = None
    data: Any | None = None
    reason_code: str | None = None


def reason_for_status(status: int, path: str) -> str:
    if status == 404:
        if any(path.startswith(prefix) for prefix in NEW_ENDPOINT_PATHS):
            return ENDPOINT_NOT_DEPLOYED
        return NOT_FOUND
    return UPSTREAM_ERROR


async def fetch_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
    *,
    timeout: float = 10.0,
) -> RouterResult:
    """GET one Router endpoint; every failure becomes a reason code."""
    try:
        response = await client.get(path, params=params, timeout=timeout)
    except httpx.ConnectError:
        return RouterResult(ok=False, reason_code=ROUTER_UNREACHABLE)
    except httpx.TimeoutException:
        return RouterResult(ok=False, reason_code=UPSTREAM_TIMEOUT)
    except Exception:
        return RouterResult(ok=False, reason_code=UPSTREAM_ERROR)
    if response.status_code == 200:
        try:
            return RouterResult(ok=True, status=200, data=response.json())
        except Exception:
            return RouterResult(ok=False, status=200, reason_code=UPSTREAM_ERROR)
    reason = reason_for_status(response.status_code, path)
    if response.status_code == 404:
        try:
            # FastAPI's unmatched-route response is exactly "Not Found".
            # A deployed endpoint uses a specific detail when its data is absent.
            detail = response.json().get("detail")
            if detail and detail != "Not Found":
                reason = NOT_FOUND
        except (ValueError, AttributeError):
            pass
    return RouterResult(
        ok=False,
        status=response.status_code,
        reason_code=reason,
    )


__all__ = [
    "RouterResult",
    "fetch_json",
    "reason_for_status",
    "NEW_ENDPOINT_PATHS",
    "ROUTER_UNREACHABLE",
    "UPSTREAM_TIMEOUT",
    "ENDPOINT_NOT_DEPLOYED",
    "NOT_FOUND",
    "UPSTREAM_ERROR",
]
