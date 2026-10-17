"""Stock intelligence page: decision card / four dimensions / risk blocks."""
from __future__ import annotations

import asyncio

import httpx

from webui import canonical
from webui.providers.symbol import normalize_symbol
from webui.services.blocks import block
from webui.services.provenance import provenance


async def stock_intel(client: httpx.AsyncClient, symbol: str) -> dict:
    canonical_symbol = normalize_symbol(symbol)
    if canonical_symbol is None:
        raise ValueError(f"invalid A-share symbol: {symbol}")

    packet_result, cross_result, context_result = await asyncio.gather(
        canonical.decision_packet_latest(client, canonical_symbol),
        canonical.cross_section_symbol(client, canonical_symbol),
        canonical.equity_context_stock(client, canonical_symbol),
    )

    if packet_result.ok:
        packet_data = packet_result.data or {}
        decision_block = block(packet_data, source="router.decision_packet")
    else:
        decision_block = block(
            reason_code=packet_result.reason_code,
            source="router.decision_packet",
        )

    if cross_result.ok:
        cross_block = block(
            cross_result.data, source="router.cross_section"
        )
    else:
        cross_block = block(
            reason_code=cross_result.reason_code,
            source="router.cross_section",
        )

    packet = packet_result.data or {}
    risks: list[dict] = []
    if packet_result.ok != cross_result.ok:
        risks.append({"code": "data_incomplete", "value": None})
    if packet:
        if packet.get("zone_expired"):
            risks.append({"code": "zone_expired", "value": packet.get("zone_valid_until")})
        if packet.get("veto_result"):
            risks.append({"code": "veto_triggered", "value": packet.get("veto_result")})
    cross_data = cross_result.data or {}
    if cross_data.get("score_source") == "legacy_60_40_fallback":
        risks.append({"code": "legacy_score", "value": "legacy_60_40_fallback"})

    context = context_result.data or {}
    identity = context.get("identity") or {
        "symbol": canonical_symbol,
        "name": cross_data.get("name"),
        "code": canonical_symbol.split(".")[0],
        "exchange": canonical_symbol.split(".", 1)[1],
    }
    identity.setdefault("code", canonical_symbol.split(".")[0])
    listing_status = str(identity.get("listing_status") or "UNKNOWN").upper()
    if listing_status not in {"ACTIVE", "LISTED", "L", "UNKNOWN"}:
        risks.append({"code": "security_not_active", "value": listing_status})
    return {
        "symbol": canonical_symbol,
        "identity": identity,
        "database_context": (
            block(context, source="router.equity_context")
            if context_result.ok else block(reason_code=context_result.reason_code, source="router.equity_context")
        ),
        "decision_card": decision_block,
        "four_dimensions": cross_block,
        "risk": {
            "available": packet_result.ok or cross_result.ok,
            "reason_code": None if packet_result.ok or cross_result.ok else packet_result.reason_code,
            "stale": False,
            "source": "webui_pure_function",
            "data": {"risks": risks},
        },
        "provenance": provenance(
            data_source="router_canonical",
            calculation_source="router_canonical",
            formal_status=(
                "formal" if packet_result.ok or cross_result.ok else "unavailable"
            ),
        ),
    }


__all__ = ["stock_intel"]
