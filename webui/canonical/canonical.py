"""Canonical-plane wrappers (read-only; single httpx touchpoint is gateway)."""
from __future__ import annotations

from typing import Any

import httpx

from webui.canonical.gateway import RouterResult, fetch_json


async def cross_section_latest(
    client: httpx.AsyncClient,
    *,
    limit: int = 100,
    offset: int = 0,
    min_composite: float | None = None,
    sort: str = "composite",
) -> RouterResult:
    params: dict[str, Any] = {"limit": limit, "offset": offset, "sort": sort}
    if min_composite is not None:
        params["min_composite"] = min_composite
    return await fetch_json(client, "/v1/cross-section/latest", params)


async def cross_section_symbol(
    client: httpx.AsyncClient, symbol: str
) -> RouterResult:
    return await fetch_json(client, f"/v1/cross-section/{symbol}")


async def decision_packet_latest(
    client: httpx.AsyncClient, symbol: str
) -> RouterResult:
    return await fetch_json(
        client, f"/v1/decision-packets/v2/{symbol}/latest"
    )


async def scanner_runs(
    client: httpx.AsyncClient, *, limit: int = 10
) -> RouterResult:
    return await fetch_json(client, "/v1/scanner/runs", {"limit": limit})


async def scanner_run_candidates(
    client: httpx.AsyncClient, run_id: str
) -> RouterResult:
    return await fetch_json(client, f"/v1/scanner/runs/{run_id}/candidates")


async def market_breadth(
    client: httpx.AsyncClient, *, maximum_age_seconds: int = 300
) -> RouterResult:
    return await fetch_json(
        client,
        "/v1/market-snapshots/latest/breadth",
        {"maximum_age_seconds": maximum_age_seconds},
    )


async def latest_quotes(
    client: httpx.AsyncClient, *, limit: int = 5000
) -> RouterResult:
    """Per-symbol quote enrichment ONLY; never used for market statistics."""
    return await fetch_json(
        client,
        "/v1/market-snapshots/latest",
        {"limit": limit},
    )


async def data_coverage(client: httpx.AsyncClient) -> RouterResult:
    return await fetch_json(client, "/v1/data-coverage/latest")


async def providers_health(client: httpx.AsyncClient) -> RouterResult:
    return await fetch_json(client, "/v1/providers/health")


async def router_health(client: httpx.AsyncClient) -> RouterResult:
    return await fetch_json(client, "/health", timeout=3.0)


async def equity_context_overview(client: httpx.AsyncClient) -> RouterResult:
    return await fetch_json(client, "/v1/equity-context/overview", {"sector_limit": 100})


async def equity_context_stock(client: httpx.AsyncClient, symbol: str) -> RouterResult:
    return await fetch_json(client, f"/v1/equity-context/stocks/{symbol}")


async def dragon_tiger_board(client: httpx.AsyncClient, *, limit: int = 50) -> RouterResult:
    return await fetch_json(client, "/v1/equity-context/dragon-tiger", {"limit": limit})


__all__ = [
    "cross_section_latest",
    "cross_section_symbol",
    "decision_packet_latest",
    "scanner_runs",
    "scanner_run_candidates",
    "market_breadth",
    "latest_quotes",
    "data_coverage",
    "providers_health",
    "router_health",
    "equity_context_overview",
    "equity_context_stock",
    "dragon_tiger_board",
]
