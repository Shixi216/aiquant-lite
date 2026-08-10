from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from config.settings import settings
from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.execution_status import compute_execution_status


SNAPSHOT_SCHEMA_VERSION = "decision-review-snapshot-v1"
FIVE_FACTOR_KEYS = (
    "technical",
    "fundamental",
    "sentiment",
    "policy_news",
    "capital_flow",
)


class SnapshotStage(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    POST_AUCTION = "POST_AUCTION"
    OPEN_5_MIN = "OPEN_5_MIN"
    TEN_OCLOCK = "TEN_OCLOCK"
    MIDDAY = "MIDDAY"
    FOURTEEN_THIRTY = "FOURTEEN_THIRTY"
    CLOSE = "CLOSE"


SUPPORTED_SNAPSHOT_STAGES = tuple(SnapshotStage)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class DecisionReviewSnapshot:
    snapshot_id: str
    decision_id: str
    decision_version: int
    symbol: str
    stage: SnapshotStage
    captured_at: datetime
    data_time: datetime
    formal_action: str
    execution_status: str
    execution_status_reason: str
    formal_score: float
    five_factor_conclusions: dict[str, Any]
    current_position_ratio: float
    target_position_ratio: float
    recommended_batches: int
    frozen_entry_zone: tuple[float, ...]
    frozen_preferred_zone: tuple[float, ...]
    frozen_stop_loss_price: float | None
    frozen_take_profit_zone: tuple[float, ...]
    veto_triggered: bool
    veto_type: str | None
    change_reason: str
    data_hash: str
    snapshot_hash: str
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    def hash_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("snapshot_hash", None)
        payload["stage"] = self.stage.value
        payload["captured_at"] = self.captured_at.astimezone(timezone.utc).isoformat()
        payload["data_time"] = self.data_time.astimezone(timezone.utc).isoformat()
        return payload

    def verify_hash(self) -> bool:
        expected = hashlib.sha256(
            _canonical_json(self.hash_payload()).encode("utf-8")
        ).hexdigest()
        return expected == self.snapshot_hash


def _change_reason(
    previous: DecisionReviewSnapshot | None,
    *,
    action: str,
    execution_status: str,
    formal_score: float,
    target_position_ratio: float,
    veto_triggered: bool,
    data_hash: str,
) -> str:
    if previous is None:
        return "INITIAL_SNAPSHOT"
    changes: list[str] = []
    if previous.formal_action != action:
        changes.append(f"ACTION:{previous.formal_action}->{action}")
    if previous.execution_status != execution_status:
        changes.append(
            f"EXECUTION:{previous.execution_status}->{execution_status}"
        )
    if abs(previous.formal_score - formal_score) >= 1e-12:
        changes.append(
            f"FORMAL_SCORE:{previous.formal_score:.6f}->{formal_score:.6f}"
        )
    if abs(previous.target_position_ratio - target_position_ratio) >= 1e-12:
        changes.append(
            "TARGET_POSITION:"
            f"{previous.target_position_ratio:.6f}->{target_position_ratio:.6f}"
        )
    if previous.veto_triggered != veto_triggered:
        changes.append(f"VETO:{previous.veto_triggered}->{veto_triggered}")
    if previous.data_hash != data_hash:
        changes.append("DATA_HASH_CHANGED")
    return ";".join(changes) if changes else "NO_MATERIAL_CHANGE"


class DecisionSnapshotRepository:
    """Append-only repository; deliberately exposes no update operation."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or settings.opc_database_path)
        self._ensure_schema()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        from database.db import open_database

        return open_database(self.database_path)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_review_snapshots (
                    snapshot_id VARCHAR PRIMARY KEY,
                    decision_id VARCHAR NOT NULL,
                    decision_version BIGINT NOT NULL,
                    symbol VARCHAR NOT NULL,
                    stage VARCHAR NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL,
                    data_time TIMESTAMPTZ NOT NULL,
                    formal_action VARCHAR NOT NULL,
                    execution_status VARCHAR NOT NULL,
                    execution_status_reason VARCHAR NOT NULL,
                    formal_score DOUBLE NOT NULL,
                    five_factor_conclusions_json JSON NOT NULL,
                    current_position_ratio DOUBLE NOT NULL,
                    target_position_ratio DOUBLE NOT NULL,
                    recommended_batches BIGINT NOT NULL,
                    frozen_entry_zone_json JSON NOT NULL,
                    frozen_preferred_zone_json JSON NOT NULL,
                    frozen_stop_loss_price DOUBLE,
                    frozen_take_profit_zone_json JSON NOT NULL,
                    veto_triggered BOOLEAN NOT NULL,
                    veto_type VARCHAR,
                    change_reason VARCHAR NOT NULL,
                    data_hash VARCHAR NOT NULL,
                    snapshot_hash VARCHAR NOT NULL,
                    schema_version VARCHAR NOT NULL,
                    CHECK (length(data_hash) = 64),
                    CHECK (length(snapshot_hash) = 64)
                )
                """
            )

    def save(self, snapshot: DecisionReviewSnapshot) -> None:
        if not snapshot.verify_hash():
            raise ValueError("decision snapshot hash verification failed")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decision_review_snapshots VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.decision_id,
                    snapshot.decision_version,
                    snapshot.symbol,
                    snapshot.stage.value,
                    snapshot.captured_at,
                    snapshot.data_time,
                    snapshot.formal_action,
                    snapshot.execution_status,
                    snapshot.execution_status_reason,
                    snapshot.formal_score,
                    _canonical_json(snapshot.five_factor_conclusions),
                    snapshot.current_position_ratio,
                    snapshot.target_position_ratio,
                    snapshot.recommended_batches,
                    _canonical_json(snapshot.frozen_entry_zone),
                    _canonical_json(snapshot.frozen_preferred_zone),
                    snapshot.frozen_stop_loss_price,
                    _canonical_json(snapshot.frozen_take_profit_zone),
                    snapshot.veto_triggered,
                    snapshot.veto_type,
                    snapshot.change_reason,
                    snapshot.data_hash,
                    snapshot.snapshot_hash,
                    snapshot.schema_version,
                ],
            )

    @staticmethod
    def _restore(row: tuple[Any, ...]) -> DecisionReviewSnapshot:
        return DecisionReviewSnapshot(
            snapshot_id=row[0],
            decision_id=row[1],
            decision_version=int(row[2]),
            symbol=row[3],
            stage=SnapshotStage(row[4]),
            captured_at=row[5],
            data_time=row[6],
            formal_action=row[7],
            execution_status=row[8],
            execution_status_reason=row[9],
            formal_score=float(row[10]),
            five_factor_conclusions=json.loads(row[11]),
            current_position_ratio=float(row[12]),
            target_position_ratio=float(row[13]),
            recommended_batches=int(row[14]),
            frozen_entry_zone=tuple(json.loads(row[15])),
            frozen_preferred_zone=tuple(json.loads(row[16])),
            frozen_stop_loss_price=(
                None if row[17] is None else float(row[17])
            ),
            frozen_take_profit_zone=tuple(json.loads(row[18])),
            veto_triggered=bool(row[19]),
            veto_type=row[20],
            change_reason=row[21],
            data_hash=row[22],
            snapshot_hash=row[23],
            schema_version=row[24],
        )

    def list_for_decision(self, decision_id: str) -> list[DecisionReviewSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM decision_review_snapshots
                WHERE decision_id = ?
                ORDER BY captured_at, snapshot_id
                """,
                [decision_id],
            ).fetchall()
        snapshots = [self._restore(row) for row in rows]
        if not all(item.verify_hash() for item in snapshots):
            raise ValueError("stored decision snapshot failed hash verification")
        return snapshots

    def latest(self, decision_id: str) -> DecisionReviewSnapshot | None:
        items = self.list_for_decision(decision_id)
        return items[-1] if items else None


class DecisionSnapshotService:
    def __init__(self, repository: DecisionSnapshotRepository) -> None:
        self.repository = repository

    def capture(
        self,
        *,
        decision_id: str,
        decision_version: int,
        symbol: str,
        stage: SnapshotStage,
        response: DecisionResponse,
        five_factor_conclusions: dict[str, Any],
        current_price: float,
        captured_at: datetime,
        data_time: datetime,
        data_hash: str,
        veto_type: str | None = None,
    ) -> DecisionReviewSnapshot:
        if captured_at.tzinfo is None or data_time.tzinfo is None:
            raise ValueError("snapshot timestamps must include timezone")
        if len(data_hash) != 64:
            raise ValueError("data_hash must be a SHA-256 hex digest")
        if set(five_factor_conclusions) != set(FIVE_FACTOR_KEYS):
            raise ValueError("all five factor conclusions are required")
        action = response.action.value
        status, reason = compute_execution_status(
            current_price=current_price,
            action=action,
            veto_triggered=response.veto_triggered,
            frozen_entry_zone=list(response.frozen_entry_zone),
            frozen_preferred_zone=list(response.frozen_preferred_zone),
            frozen_stop_loss_price=response.frozen_stop_loss_price,
        )
        previous = self.repository.latest(decision_id)
        change_reason = _change_reason(
            previous,
            action=action,
            execution_status=status,
            formal_score=response.formal_score,
            target_position_ratio=response.target_position_ratio,
            veto_triggered=response.veto_triggered,
            data_hash=data_hash,
        )
        common = {
            "snapshot_id": "drs_" + uuid4().hex[:24],
            "decision_id": decision_id,
            "decision_version": decision_version,
            "symbol": symbol,
            "stage": stage,
            "captured_at": captured_at,
            "data_time": data_time,
            "formal_action": action,
            "execution_status": status,
            "execution_status_reason": reason,
            "formal_score": float(response.formal_score),
            "five_factor_conclusions": dict(five_factor_conclusions),
            "current_position_ratio": float(response.current_position_ratio),
            "target_position_ratio": float(response.target_position_ratio),
            "recommended_batches": int(response.recommended_batches),
            "frozen_entry_zone": tuple(float(value) for value in response.frozen_entry_zone),
            "frozen_preferred_zone": tuple(
                float(value) for value in response.frozen_preferred_zone
            ),
            "frozen_stop_loss_price": (
                None
                if response.frozen_stop_loss_price is None
                else float(response.frozen_stop_loss_price)
            ),
            "frozen_take_profit_zone": tuple(
                float(value) for value in response.price_zones.take_profit_zone
            ),
            "veto_triggered": response.veto_triggered,
            "veto_type": veto_type,
            "change_reason": change_reason,
            "data_hash": data_hash,
            "snapshot_hash": "",
        }
        provisional = DecisionReviewSnapshot(**common)
        digest = hashlib.sha256(
            _canonical_json(provisional.hash_payload()).encode("utf-8")
        ).hexdigest()
        snapshot = DecisionReviewSnapshot(**{**common, "snapshot_hash": digest})
        self.repository.save(snapshot)
        return snapshot


__all__ = [
    "FIVE_FACTOR_KEYS",
    "SNAPSHOT_SCHEMA_VERSION",
    "SUPPORTED_SNAPSHOT_STAGES",
    "DecisionReviewSnapshot",
    "DecisionSnapshotRepository",
    "DecisionSnapshotService",
    "SnapshotStage",
]
