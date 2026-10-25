"""Schemas for the frozen DecisionPacket V2 read endpoint."""
from __future__ import annotations

from pydantic import BaseModel


class FrozenZone(BaseModel):
    low: float
    high: float


class DecisionPacketV2Response(BaseModel):
    decision_id: str
    symbol: str
    action: str
    action_zh: str
    confidence: float
    formal_score: float
    enhanced_score: float | None = None
    data_cutoff: str
    created_at: str
    expires_at: str
    zone_version: str
    zone_generated_at: str
    zone_valid_until: str
    zone_expired: bool | None = None
    reference_price: float
    has_frozen_plan: bool
    frozen_entry_zone: FrozenZone | None = None
    frozen_preferred_zone: FrozenZone | None = None
    frozen_stop_loss_price: float | None = None
    veto_result: dict | None = None
    evidence_ids: list[str]


__all__ = ["DecisionPacketV2Response", "FrozenZone"]
