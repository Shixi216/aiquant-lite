from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from database.db import get_connection, initialize_database
from trading.experiments.overheat_penalty_shadow import (
    PenaltyCalibration,
    PenaltyComponent,
    shadow_score,
)


ALGORITHM_VERSION = "overheat-preproduction-shadow-v1"
LABEL_VERSION = "overheat-preproduction-forward-return-v1"
HORIZONS = (1, 3, 5, 10, 20)
FIXED_CALIBRATION = PenaltyCalibration(
    expansion=PenaltyComponent(
        field="ma20_ma60_expansion",
        median=0.13222514188749512,
        q75=0.20292835976991452,
        weight=0.07034588503355267,
    ),
    ma60_slope=PenaltyComponent(
        field="ma60_slope_5",
        median=0.03427116087581861,
        q75=0.048701541558149986,
        weight=0.07974481983998552,
    ),
    maximum_component_multiplier=2.0,
)


SHADOW_VERSION = "B-OVERHEAT-1.0.0"
SHADOW_EFFECTIVE_AT = datetime.fromisoformat("2026-08-10T11:52:42+08:00")
SHADOW_CODE_VERSION = ALGORITHM_VERSION
SHADOW_FORMULA = (
    "shadow_technical_score=original_technical_score-"
    "expansion_penalty-ma60_slope_penalty"
)


def frozen_parameter_payload() -> dict[str, Any]:
    return {
        "formula": SHADOW_FORMULA,
        "expansion_speed_penalized": False,
        "calibration": FIXED_CALIBRATION.as_dict(),
    }


FROZEN_PARAMETER_HASH = hashlib.sha256(
    json.dumps(
        frozen_parameter_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
EXPECTED_FROZEN_PARAMETER_HASH = (
    "b976fa673c49520732825787b5da52ec792d8792ac378549fcb7d683a837e1bb"
)
if FROZEN_PARAMETER_HASH != EXPECTED_FROZEN_PARAMETER_HASH:
    raise RuntimeError("frozen B shadow parameters changed")


@dataclass(frozen=True)
class PricePoint:
    bar_id: str
    event_time: datetime
    data_available_time: datetime
    close: float
    high: float
    low: float


@dataclass(frozen=True)
class OverheatFeatures:
    status: str
    bar_history_count: int
    source_bar_id: str | None = None
    source_close: float | None = None
    ma20_ma60_expansion: float | None = None
    ma60_slope_5: float | None = None


def calculate_overheat_features(points: list[PricePoint]) -> OverheatFeatures:
    ordered = sorted(points, key=lambda item: (item.event_time, item.bar_id))
    if len(ordered) < 65:
        return OverheatFeatures(
            status="INSUFFICIENT_HISTORY",
            bar_history_count=len(ordered),
            source_bar_id=(ordered[-1].bar_id if ordered else None),
            source_close=(ordered[-1].close if ordered else None),
        )
    closes = [item.close for item in ordered[-65:]]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    prior_ma60 = sum(closes[:60]) / 60
    if ma60 <= 0 or prior_ma60 <= 0:
        return OverheatFeatures(
            status="INVALID_PRICE_HISTORY",
            bar_history_count=len(ordered),
            source_bar_id=ordered[-1].bar_id,
            source_close=ordered[-1].close,
        )
    return OverheatFeatures(
        status="AVAILABLE",
        bar_history_count=len(ordered),
        source_bar_id=ordered[-1].bar_id,
        source_close=ordered[-1].close,
        ma20_ma60_expansion=ma20 / ma60 - 1,
        ma60_slope_5=ma60 / prior_ma60 - 1,
    )


class OverheatShadowRepository:
    def __init__(self) -> None:
        initialize_database()

    def recent_points(
        self, symbol: str, data_cutoff: datetime, *, limit: int = 65,
    ) -> list[PricePoint]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT bar_id, event_time, data_available_time,
                       close, high, low
                FROM canonical_historical_bars
                WHERE symbol = ?
                  AND adjustment_type = 'RAW'
                  AND event_time <= ?
                  AND data_available_time <= ?
                  AND data_cutoff <= ?
                  AND verification_status <> 'CONFLICT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY CAST(event_time AS DATE)
                    ORDER BY data_cutoff DESC, bar_id DESC
                ) = 1
                ORDER BY event_time DESC, bar_id DESC
                LIMIT ?
                """,
                [symbol, data_cutoff, data_cutoff, data_cutoff, limit],
            ).fetchall()
        return [
            PricePoint(
                bar_id=row[0], event_time=row[1], data_available_time=row[2],
                close=float(row[3]), high=float(row[4]), low=float(row[5]),
            )
            for row in reversed(rows)
        ]

    def save_observation(self, values: dict[str, Any]) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO overheat_shadow_observations (
                    observation_id, symbol, observed_at, data_cutoff,
                    source_bar_id, source_close, bar_history_count,
                    feature_status, original_technical_score,
                    shadow_technical_score, ma20_ma60_expansion,
                    ma60_slope_5, expansion_penalty, ma60_slope_penalty,
                    total_penalty, a_formal_score, b_shadow_formal_score,
                    a_action, b_shadow_action, action_diverged, hard_veto,
                    official_result_hash, algorithm_version,
                    calibration_json, created_at, research_only,
                    affects_production, creates_trade_records,
                    shadow_version, parameter_hash, shadow_code_version
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, TRUE, FALSE, FALSE, ?, ?, ?
                )
                """,
                [
                    values["observation_id"], values["symbol"],
                    values["observed_at"], values["data_cutoff"],
                    values["source_bar_id"], values["source_close"],
                    values["bar_history_count"], values["feature_status"],
                    values["original_technical_score"],
                    values["shadow_technical_score"],
                    values["ma20_ma60_expansion"], values["ma60_slope_5"],
                    values["expansion_penalty"],
                    values["ma60_slope_penalty"], values["total_penalty"],
                    values["a_formal_score"], values["b_shadow_formal_score"],
                    values["a_action"], values["b_shadow_action"],
                    values["action_diverged"], values["hard_veto"],
                    values["official_result_hash"], ALGORITHM_VERSION,
                    json.dumps(FIXED_CALIBRATION.as_dict(), sort_keys=True),
                    values["created_at"], SHADOW_VERSION,
                    FROZEN_PARAMETER_HASH, SHADOW_CODE_VERSION,
                ],
            )

    def pending_observations(
        self, as_of: datetime, *, limit: int = 100,
    ) -> list[tuple[Any, ...]]:
        with get_connection() as connection:
            return connection.execute(
                """
                SELECT o.observation_id, o.symbol, o.data_cutoff,
                       o.source_close
                FROM overheat_shadow_observations o
                LEFT JOIN overheat_shadow_forward_labels l
                  ON l.observation_id = o.observation_id
                 AND l.label_version = ?
                WHERE o.source_close IS NOT NULL
                  AND o.data_cutoff < ?
                GROUP BY o.observation_id, o.symbol, o.data_cutoff,
                         o.source_close
                HAVING COUNT(l.label_id) < 5
                ORDER BY o.data_cutoff, o.observation_id
                LIMIT ?
                """,
                [LABEL_VERSION, as_of, limit],
            ).fetchall()

    def future_points(
        self, symbol: str, data_cutoff: datetime, as_of: datetime,
    ) -> list[PricePoint]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT bar_id, event_time, data_available_time,
                       close, high, low
                FROM canonical_historical_bars
                WHERE symbol = ?
                  AND adjustment_type = 'RAW'
                  AND event_time > ?
                  AND event_time <= ?
                  AND data_available_time <= ?
                  AND data_cutoff <= ?
                  AND verification_status <> 'CONFLICT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY CAST(event_time AS DATE)
                    ORDER BY data_cutoff DESC, bar_id DESC
                ) = 1
                ORDER BY event_time, bar_id
                LIMIT 20
                """,
                [symbol, data_cutoff, as_of, as_of, as_of],
            ).fetchall()
        return [
            PricePoint(
                bar_id=row[0], event_time=row[1], data_available_time=row[2],
                close=float(row[3]), high=float(row[4]), low=float(row[5]),
            )
            for row in rows
        ]

    def save_label(
        self, *, observation_id: str, symbol: str, horizon: int,
        source_close: float, points: list[PricePoint], calculated_at: datetime,
    ) -> None:
        selected = points[:horizon]
        exit_point = selected[-1]
        label_id = "ohl_" + hashlib.sha256(
            f"{observation_id}:{horizon}:{LABEL_VERSION}".encode("utf-8")
        ).hexdigest()[:32]
        with get_connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO overheat_shadow_forward_labels VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE
                )
                """,
                [
                    label_id, observation_id, symbol, horizon,
                    exit_point.event_time.date(), exit_point.close,
                    exit_point.close / source_close - 1,
                    max(item.high / source_close - 1 for item in selected),
                    min(item.low / source_close - 1 for item in selected),
                    max(item.data_available_time for item in selected),
                    calculated_at, LABEL_VERSION,
                ],
            )


class PreproductionOverheatShadowService:
    def __init__(
        self,
        repository: OverheatShadowRepository | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or OverheatShadowRepository()
        self.clock = clock or (lambda: datetime.now().astimezone())

    def record(
        self,
        request: Any,
        a_result: Any,
        formal_calculator: Callable[[Any], Any],
    ) -> str:
        if (
            isinstance(self.repository, OverheatShadowRepository)
            and os.environ.get("PYTEST_CURRENT_TEST")
        ):
            return "ohs_pytest_side_effect_suppressed"
        points = self.repository.recent_points(
            request.symbol, request.data_cutoff,
        )
        features = calculate_overheat_features(points)
        if features.status == "AVAILABLE":
            scored = shadow_score(
                {
                    "technical_score": request.technical_score,
                    "ma20_ma60_expansion": features.ma20_ma60_expansion,
                    "ma60_slope_5": features.ma60_slope_5,
                },
                FIXED_CALIBRATION,
            )
        else:
            scored = {
                "shadow_technical_score": float(request.technical_score),
                "expansion_penalty": 0.0,
                "ma60_slope_penalty": 0.0,
                "total_penalty": 0.0,
            }
        shadow_technical = max(
            -1.0, min(1.0, float(scored["shadow_technical_score"])),
        )
        b_request = request.model_copy(
            update={
                "technical_score": shadow_technical,
                "persist_shadow": False,
            }
        )
        b_result = formal_calculator(b_request)
        a_action = a_result.final_action.value
        b_action = b_result.final_action.value
        official_json = json.dumps(
            a_result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = self.clock()
        observation_id = f"ohs_{uuid.uuid4().hex}"
        self.repository.save_observation(
            {
                "observation_id": observation_id,
                "symbol": request.symbol,
                "observed_at": now,
                "data_cutoff": request.data_cutoff,
                "source_bar_id": features.source_bar_id,
                "source_close": features.source_close,
                "bar_history_count": features.bar_history_count,
                "feature_status": features.status,
                "original_technical_score": float(request.technical_score),
                "shadow_technical_score": shadow_technical,
                "ma20_ma60_expansion": features.ma20_ma60_expansion,
                "ma60_slope_5": features.ma60_slope_5,
                "expansion_penalty": float(scored["expansion_penalty"]),
                "ma60_slope_penalty": float(scored["ma60_slope_penalty"]),
                "total_penalty": float(scored["total_penalty"]),
                "a_formal_score": float(a_result.score),
                "b_shadow_formal_score": float(b_result.score),
                "a_action": a_action,
                "b_shadow_action": b_action,
                "action_diverged": a_action != b_action,
                "hard_veto": bool(request.hard_veto),
                "official_result_hash": hashlib.sha256(
                    official_json.encode("utf-8")
                ).hexdigest(),
                "shadow_version": SHADOW_VERSION,
                "parameter_hash": FROZEN_PARAMETER_HASH,
                "shadow_code_version": SHADOW_CODE_VERSION,
                "created_at": now,
            }
        )
        self.update_matured_outcomes(as_of=request.data_cutoff)
        return observation_id

    def update_matured_outcomes(
        self, *, as_of: datetime, limit: int = 100,
    ) -> int:
        saved = 0
        for observation_id, symbol, data_cutoff, source_close in (
            self.repository.pending_observations(as_of, limit=limit)
        ):
            points = self.repository.future_points(symbol, data_cutoff, as_of)
            for horizon in HORIZONS:
                if len(points) < horizon:
                    continue
                self.repository.save_label(
                    observation_id=observation_id,
                    symbol=symbol,
                    horizon=horizon,
                    source_close=float(source_close),
                    points=points,
                    calculated_at=self.clock(),
                )
                saved += 1
        return saved


__all__ = [
    "ALGORITHM_VERSION",
    "EXPECTED_FROZEN_PARAMETER_HASH",
    "FIXED_CALIBRATION",
    "FROZEN_PARAMETER_HASH",
    "HORIZONS",
    "SHADOW_CODE_VERSION",
    "SHADOW_EFFECTIVE_AT",
    "SHADOW_FORMULA",
    "SHADOW_VERSION",
    "OverheatFeatures",
    "OverheatShadowRepository",
    "PreproductionOverheatShadowService",
    "PricePoint",
    "calculate_overheat_features",
    "frozen_parameter_payload",
]
