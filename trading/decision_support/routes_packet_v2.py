"""GET-only frozen DecisionPacket V2 endpoint.

The frozen zones are returned verbatim; no caller may recompute them from
live prices.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from trading.decision_support.action import action_zh
from trading.decision_support.decision_packet_v2 import (
    DecisionPacketV2,
    DecisionPacketV2Repository,
)
from trading.decision_support.schemas_packet_v2 import (
    DecisionPacketV2Response,
    FrozenZone,
)

TZ = timezone(timedelta(hours=8))


router = APIRouter(prefix="/v1/decision-packets/v2", tags=["decision-packets-v2"])


def _zone(values: list) -> FrozenZone | None:
    if len(values) >= 2:
        try:
            return FrozenZone(low=float(values[0]), high=float(values[1]))
        except (TypeError, ValueError):
            return None
    return None


def _zone_expired(packet: DecisionPacketV2) -> bool | None:
    if not packet.zone_valid_until:
        return None
    try:
        valid_until = datetime.fromisoformat(packet.zone_valid_until)
    except ValueError:
        return None
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=TZ)
    return datetime.now(tz=TZ) > valid_until


def _to_response(packet: DecisionPacketV2) -> DecisionPacketV2Response:
    return DecisionPacketV2Response(
        decision_id=packet.decision_id,
        symbol=packet.symbol,
        action=packet.action,
        action_zh=action_zh(packet.action),
        confidence=packet.confidence,
        formal_score=packet.formal_score,
        enhanced_score=packet.enhanced_score,
        data_cutoff=packet.data_cutoff,
        created_at=packet.created_at,
        expires_at=packet.expires_at,
        zone_version=packet.zone_version,
        zone_generated_at=packet.zone_generated_at,
        zone_valid_until=packet.zone_valid_until,
        zone_expired=_zone_expired(packet),
        reference_price=packet.reference_price,
        has_frozen_plan=bool(packet.frozen_entry_zone),
        frozen_entry_zone=_zone(packet.frozen_entry_zone),
        frozen_preferred_zone=_zone(packet.frozen_preferred_zone),
        frozen_stop_loss_price=packet.frozen_stop_loss_price,
        veto_result=packet.veto_result or None,
        evidence_ids=[str(item) for item in packet.evidence_ids],
    )


@router.get(
    "/{symbol}/latest",
    response_model=DecisionPacketV2Response,
    summary="Read the latest frozen decision packet for one symbol",
)
def latest_packet(
    symbol: str,
    as_of: str | None = Query(default=None),
    local_user_id: str | None = Query(default=None),
) -> DecisionPacketV2Response:
    # Repository resolves the database path per request so tests can
    # monkeypatch settings.opc_database_path after import.
    packet = DecisionPacketV2Repository().latest_by_symbol(
        symbol,
        as_of=as_of,
        local_user_id=local_user_id,
    )
    if packet is None:
        raise HTTPException(
            status_code=404,
            detail=f"no frozen decision packet for {symbol}",
        )
    return _to_response(packet)


__all__ = ["router"]
