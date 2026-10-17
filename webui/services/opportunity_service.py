"""Opportunities page: four-dimension cross-section ranking first.

The formal cross-section is the primary ordering; scanner buckets and
per-symbol quotes are overlays. Quotes are NEVER used for market-level
statistics and missing quotes stay missing (no zero-filling).
"""
from __future__ import annotations

import asyncio

import httpx

from webui import canonical
from webui.services.blocks import block
from webui.services.provenance import provenance

BUCKET_KEYS = {"CORE": "CORE", "NEAR": "NEAR", "CONTROL": "CONTROL"}


async def opportunities(
    client: httpx.AsyncClient,
    *,
    limit: int = 50,
    offset: int = 0,
    min_score: float | None = None,
    sort: str = "composite",
) -> dict:
    cross_result, runs_result = await asyncio.gather(
        canonical.cross_section_latest(
            client,
            limit=limit,
            offset=offset,
            min_composite=min_score,
            sort=sort,
        ),
        canonical.scanner_runs(client, limit=1),
    )

    if not cross_result.ok:
        cross_block = block(
            reason_code=cross_result.reason_code,
            source="router.cross_section",
        )
        items = []
        total = 0
        thresholds = None
        data_cutoff = None
    else:
        data = cross_result.data or {}
        items = data.get("items", [])
        total = data.get("total", 0)
        thresholds = data.get("thresholds")
        data_cutoff = data.get("data_cutoff")
        cross_block = block(data, source="router.cross_section")

    # scanner bucket overlay from the latest persisted run
    bucket_by_symbol: dict[str, str] = {}
    classification_source = "unavailable"
    if runs_result.ok:
        runs = (runs_result.data or {}).get("items", [])
        if runs:
            run_id = runs[0]["run_id"]
            candidates_result = await canonical.scanner_run_candidates(
                client, run_id
            )
            if candidates_result.ok:
                for candidate in (candidates_result.data or {}).get(
                    "candidates", []
                ):
                    symbol = candidate.get("symbol")
                    bucket = candidate.get("bucket") or candidate.get(
                        "classification"
                    )
                    if symbol and bucket:
                        bucket_by_symbol[str(symbol).upper()] = str(bucket)
                        classification_source = f"scanner_run:{run_id}"

    enriched = []
    for item in items:
        symbol = item.get("symbol")
        enriched.append(
            {
                **item,
                "scanner_bucket": bucket_by_symbol.get(
                    str(symbol).upper()
                ),
            }
        )

    return {
        "classification_source": classification_source,
        "ranking_data_cutoff": data_cutoff,
        "thresholds": thresholds,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "items": enriched,
        "cross_section_block": cross_block,
        "provenance": provenance(
            data_source="router_canonical",
            calculation_source="router_canonical",
            formal_status="formal" if cross_result.ok else "unavailable",
        ),
    }


async def attach_quotes(
    client: httpx.AsyncClient, payload: dict
) -> dict:
    """Optional quote overlay; called separately so failures degrade alone."""
    quotes_result = await canonical.latest_quotes(client)
    if not quotes_result.ok:
        payload["quote_block"] = block(
            reason_code=quotes_result.reason_code,
            source="router.market_snapshot",
        )
        return payload
    by_symbol = {
        item.get("symbol"): item
        for item in (quotes_result.data or {}).get("items", [])
    }
    for item in payload["items"]:
        quote = by_symbol.get(item.get("symbol"))
        item["quote"] = (
            {
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "turnover_rate": quote.get("turnover_rate"),
            }
            if quote
            else None
        )
    payload["quote_block"] = block(
        {"snapshot_time": (quotes_result.data or {}).get("snapshot_time")},
        source="router.market_snapshot",
    )
    return payload


__all__ = ["opportunities", "attach_quotes", "BUCKET_KEYS"]
