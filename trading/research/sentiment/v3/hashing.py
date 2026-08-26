from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from trading.research.sentiment.v3.schemas import SnapshotType


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported hash input: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def stable_content_hash(
    *,
    snapshot_type: SnapshotType,
    source: str,
    trade_date: date,
    symbol_scope: str,
    schema_version: str,
    algorithm_version: str,
    normalized_payload: dict[str, Any],
) -> str:
    """Hash only stable source content and calculation versions.

    Collection time, creation time, and database revision are deliberately
    excluded, so repeated collection of unchanged content deduplicates.
    """

    canonical = canonical_json(
        {
            "snapshot_type": snapshot_type,
            "source": source,
            "trade_date": trade_date,
            "symbol_scope": symbol_scope,
            "schema_version": schema_version,
            "algorithm_version": algorithm_version,
            "payload": normalized_payload,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_id(
    snapshot_type: SnapshotType, content_hash: str, record_source: str | None = None
) -> str:
    suffix = "" if record_source is None else f"_{record_source.lower()}"
    return f"sv3_{snapshot_type.value.lower()}_{content_hash[:32]}{suffix}"


__all__ = ["canonical_json", "snapshot_id", "stable_content_hash"]
