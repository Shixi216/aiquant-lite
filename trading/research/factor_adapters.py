from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.fundamental.analysis import fundamental_signal
from trading.research.technical.analysis import technical_signal
from trading.schemas import Bar, FundamentalSnapshot


TECHNICAL_FACTOR_VERSION = "technical-signal-v1"
FUNDAMENTAL_FACTOR_VERSION = "fundamental-signal-v1"


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _factor_id(
    *,
    symbol: str,
    factor_type: FactorType,
    data_cutoff: datetime,
    algorithm_version: str,
    input_snapshot_hash: str,
    shadow_mode: bool,
) -> str:
    return "fac_" + _stable_hash(
        {
            "symbol": symbol,
            "factor_type": factor_type.value,
            "data_cutoff": data_cutoff.isoformat(),
            "algorithm_version": algorithm_version,
            "input_snapshot_hash": input_snapshot_hash,
            "shadow_mode": shadow_mode,
        }
    )[:32]


def technical_factor_output(
    *,
    symbol: str,
    bars: list[Bar],
    evidence_ids: list[str],
    data_cutoff: datetime,
    generated_at: datetime | None = None,
    repository: FactorOutputRepository | None = None,
) -> FactorOutput:
    signal = technical_signal(bars)
    actual_generated_at = generated_at or datetime.now().astimezone()
    if actual_generated_at < data_cutoff:
        actual_generated_at = data_cutoff
    snapshot_hash = _stable_hash(
        {
            "bars": [
                bar.model_dump(mode="json")
                for bar in sorted(bars, key=lambda item: item.trade_date)
            ],
            "evidence_ids": evidence_ids,
        }
    )
    output = FactorOutput(
        factor_id=_factor_id(
            symbol=symbol,
            factor_type=FactorType.TECHNICAL,
            data_cutoff=data_cutoff,
            algorithm_version=TECHNICAL_FACTOR_VERSION,
            input_snapshot_hash=snapshot_hash,
            shadow_mode=True,
        ),
        symbol=symbol,
        factor_type=FactorType.TECHNICAL,
        score=signal.score,
        confidence=signal.confidence,
        data_cutoff=data_cutoff,
        generated_at=actual_generated_at,
        evidence_ids=evidence_ids,
        risk_flags=signal.risks,
        model_call_ids=[],
        algorithm_version=TECHNICAL_FACTOR_VERSION,
        input_snapshot_hash=snapshot_hash,
        shadow_mode=True,
        metadata={
            "source_role": signal.role,
            "structured_summary": signal.summary,
        },
    )
    return repository.save(output) if repository is not None else output


def fundamental_factor_output(
    *,
    symbol: str,
    snapshot: FundamentalSnapshot,
    evidence_ids: list[str],
    data_cutoff: datetime,
    generated_at: datetime | None = None,
    repository: FactorOutputRepository | None = None,
    shadow_mode: bool = True,
    confidence_override: float | None = None,
    risk_flags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> FactorOutput:
    signal = fundamental_signal(snapshot)
    actual_generated_at = generated_at or datetime.now().astimezone()
    if actual_generated_at < data_cutoff:
        actual_generated_at = data_cutoff
    snapshot_hash = _stable_hash(
        {
            "snapshot": snapshot.model_dump(mode="json"),
            "evidence_ids": evidence_ids,
        }
    )
    output = FactorOutput(
        factor_id=_factor_id(
            symbol=symbol,
            factor_type=FactorType.FUNDAMENTAL,
            data_cutoff=data_cutoff,
            algorithm_version=FUNDAMENTAL_FACTOR_VERSION,
            input_snapshot_hash=snapshot_hash,
            shadow_mode=shadow_mode,
        ),
        symbol=symbol,
        factor_type=FactorType.FUNDAMENTAL,
        score=signal.score,
        confidence=(
            signal.confidence
            if confidence_override is None
            else max(0.0, min(1.0, confidence_override))
        ),
        data_cutoff=data_cutoff,
        generated_at=actual_generated_at,
        evidence_ids=evidence_ids,
        risk_flags=list(
            dict.fromkeys([*signal.risks, *(risk_flags or [])])
        ),
        model_call_ids=[],
        algorithm_version=FUNDAMENTAL_FACTOR_VERSION,
        input_snapshot_hash=snapshot_hash,
        shadow_mode=shadow_mode,
        metadata={
            "source_role": signal.role,
            "structured_summary": signal.summary,
            **(metadata or {}),
        },
    )
    return repository.save(output) if repository is not None else output


__all__ = [
    "FUNDAMENTAL_FACTOR_VERSION",
    "TECHNICAL_FACTOR_VERSION",
    "fundamental_factor_output",
    "technical_factor_output",
]
