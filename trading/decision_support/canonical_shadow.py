from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from database.db import get_connection
from trading.decision_support.canonical_input import (
    CanonicalDecisionInputBuilder,
    CanonicalVetoContext,
)
from trading.decision_support.decision_engine import DecisionEngine
from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.formal_score_resolver import LEGACY_SOURCE
from trading.decision_support.manual_position_manager import ManualPositionManager
from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.user_context import UserContext
from trading.research.capital_flow.models import MarketBar
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    FactorBundle,
    FormalResult,
)

class ShadowRecordSource(str, Enum):
    REAL = "REAL"
    TEST = "TEST"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


def runtime_shadow_record_source() -> ShadowRecordSource:
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        return ShadowRecordSource.TEST
    return ShadowRecordSource.REAL


class DivergenceCategory(str, Enum):
    EXPECTED_THRESHOLD_DIFFERENCE = "EXPECTED_THRESHOLD_DIFFERENCE"
    SCORE_SOURCE_MISMATCH = "SCORE_SOURCE_MISMATCH"
    PIT_DIFFERENCE = "PIT_DIFFERENCE"
    DATA_STATUS_DIFFERENCE = "DATA_STATUS_DIFFERENCE"
    VETO_DIFFERENCE = "VETO_DIFFERENCE"
    VETO_INPUT_INCOMPLETE = "VETO_INPUT_INCOMPLETE"
    POSITION_CONTEXT_DIFFERENCE = "POSITION_CONTEXT_DIFFERENCE"
    ACCOUNT_CONTEXT_ASSUMPTION = "ACCOUNT_CONTEXT_ASSUMPTION"
    LEGACY_MISSING_SEMANTICS = "LEGACY_MISSING_SEMANTICS"
    CANONICAL_ERROR = "CANONICAL_ERROR"
    UNKNOWN_DIVERGENCE = "UNKNOWN_DIVERGENCE"


@dataclass(frozen=True)
class CanonicalShadowRun:
    response: DecisionResponse
    score_source_mismatch: bool
    score_source_reason: str
    veto_source: str
    veto_reason: str
    veto_input_complete: bool
    divergence_categories: tuple[str, ...]
    latency_canonical_ms: float


class _NoLivePermission:
    @staticmethod
    def can_live_trade(local_user_id: str) -> bool:
        return False


class _NoTradePlanRepository:
    @staticmethod
    def create_plan(plan: object) -> None:
        raise RuntimeError("canonical decision shadow cannot create TradePlan")


def formal_score_bucket(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score < -0.5:
        return "LT_-0.5"
    if score < -0.2:
        return "-0.5_TO_-0.2"
    if score < 0.2:
        return "-0.2_TO_0.2"
    if score < 0.4:
        return "0.2_TO_0.4"
    if score < 0.65:
        return "0.4_TO_0.65"
    return "GE_0.65"


def classify_divergence(
    *,
    legacy_score: float | None,
    canonical_score: float | None,
    legacy_action: str | None,
    canonical_action: str | None,
    legacy_veto: bool | None,
    canonical_veto: bool | None,
    score_source_mismatch: bool,
    canonical_pit: bool | None,
    canonical_data_status: str | None,
    veto_input_complete: bool,
    canonical_current_position: float | None,
    account_context_assumption: bool,
    canonical_error: str = "",
    score_diverged: bool | None = None,
    pit_diverged: bool | None = None,
) -> tuple[str, ...]:
    categories: list[str] = []
    score_equal = (
        legacy_score is not None
        and canonical_score is not None
        and abs(legacy_score - canonical_score) <= 1e-12
    )
    action_diverged = legacy_action != canonical_action
    veto_diverged = legacy_veto != canonical_veto
    score_is_diverged = (
        not score_equal if score_diverged is None else score_diverged
    )
    pit_is_diverged = (
        canonical_pit is False if pit_diverged is None else pit_diverged
    )
    if canonical_error:
        categories.append(DivergenceCategory.CANONICAL_ERROR.value)
    if action_diverged and score_equal:
        categories.append(
            DivergenceCategory.EXPECTED_THRESHOLD_DIFFERENCE.value
        )
    if score_source_mismatch:
        categories.append(DivergenceCategory.SCORE_SOURCE_MISMATCH.value)
    if pit_is_diverged:
        categories.append(DivergenceCategory.PIT_DIFFERENCE.value)
    if canonical_data_status not in (None, "FRESH"):
        categories.append(DivergenceCategory.DATA_STATUS_DIFFERENCE.value)
    if veto_diverged:
        categories.append(DivergenceCategory.VETO_DIFFERENCE.value)
    if not veto_input_complete:
        categories.append(DivergenceCategory.VETO_INPUT_INCOMPLETE.value)
    if canonical_current_position not in (None, 0.0):
        categories.append(
            DivergenceCategory.POSITION_CONTEXT_DIFFERENCE.value
        )
    if account_context_assumption:
        categories.append(
            DivergenceCategory.ACCOUNT_CONTEXT_ASSUMPTION.value
        )
    if canonical_action is not None:
        categories.append(DivergenceCategory.LEGACY_MISSING_SEMANTICS.value)

    explanatory = {
        DivergenceCategory.EXPECTED_THRESHOLD_DIFFERENCE.value,
        DivergenceCategory.SCORE_SOURCE_MISMATCH.value,
        DivergenceCategory.PIT_DIFFERENCE.value,
        DivergenceCategory.DATA_STATUS_DIFFERENCE.value,
        DivergenceCategory.VETO_DIFFERENCE.value,
        DivergenceCategory.VETO_INPUT_INCOMPLETE.value,
        DivergenceCategory.POSITION_CONTEXT_DIFFERENCE.value,
        DivergenceCategory.ACCOUNT_CONTEXT_ASSUMPTION.value,
        DivergenceCategory.CANONICAL_ERROR.value,
    }
    has_unexplained_divergence = (
        score_is_diverged or action_diverged or veto_diverged or pit_is_diverged
    ) and not any(category in explanatory for category in categories)
    if has_unexplained_divergence:
        categories.append(DivergenceCategory.UNKNOWN_DIVERGENCE.value)
    return tuple(dict.fromkeys(categories))


class CanonicalDecisionShadowRepository:
    _JSON_FIELDS = {
        "canonical_price_zones_json",
        "divergence_categories_json",
    }

    def save(self, values: dict[str, Any]) -> None:
        payload = dict(values)
        source = payload.get("record_source", ShadowRecordSource.TEST.value)
        payload["record_source"] = ShadowRecordSource(source).value
        for field in self._JSON_FIELDS:
            payload[field] = json.dumps(
                payload[field], ensure_ascii=False, sort_keys=True
            )
        columns = tuple(payload)
        placeholders = ", ".join("?" for _ in columns)
        with get_connection() as connection:
            connection.execute(
                f"""
                INSERT INTO canonical_shadow_observations_v2 (
                    {", ".join(columns)}
                ) VALUES ({placeholders})
                """,
                [payload[column] for column in columns],
            )

    def save_error_observation(
        self,
        *,
        request_id: str,
        user_context: UserContext,
        request: DecisionShadowRequest,
        legacy_result: FormalResult | None,
        legacy_latency_ms: float,
        canonical_latency_ms: float,
        record_source: ShadowRecordSource | str = ShadowRecordSource.TEST,
        legacy_error: str = "",
        canonical_error: str = "",
    ) -> None:
        legacy_score = legacy_result.score if legacy_result is not None else None
        legacy_action = (
            legacy_result.final_action.value.upper()
            if legacy_result is not None
            else None
        )
        legacy_veto = (
            bool(legacy_result.hard_veto)
            if legacy_result is not None
            else None
        )
        categories = (
            ["CANONICAL_ERROR"] if canonical_error else []
        )
        self.save(
            {
                "observation_id": f"cds2_{uuid.uuid4().hex}",
                "request_id": request_id,
                "record_source": ShadowRecordSource(record_source).value,
                "symbol": request.symbol,
                "local_user_id": user_context.local_user_id,
                "external_user_id": user_context.external_user_id,
                "channel": user_context.channel,
                "user_context_source": user_context.source,
                "user_authenticated": user_context.authenticated,
                "observed_at": datetime.now().astimezone(),
                "data_cutoff": request.data_cutoff,
                "snapshot_id": None,
                "legacy_score": legacy_score,
                "canonical_score": None,
                "legacy_technical_score": None,
                "legacy_fundamental_score": None,
                "canonical_technical_score": None,
                "canonical_fundamental_score": None,
                "legacy_action": legacy_action,
                "canonical_action": None,
                "legacy_veto": legacy_veto,
                "canonical_veto": None,
                "canonical_pit": None,
                "canonical_data_status": None,
                "score_source_status": "NOT_EVALUATED",
                "veto_input_status": "VETO_INPUT_INCOMPLETE",
                "veto_source": "HISTORICAL_CANDIDATE_VETO",
                "veto_reason": "NOT_EVALUATED",
                "veto_event_count": 0,
                "account_context_source": "NOT_EVALUATED",
                "canonical_current_position": None,
                "canonical_target_position": None,
                "canonical_price_zones_json": {},
                "canonical_execution_status": None,
                "latency_legacy_ms": max(0.0, legacy_latency_ms),
                "latency_canonical_ms": max(0.0, canonical_latency_ms),
                "shadow_overhead_ms": max(0.0, canonical_latency_ms),
                "legacy_success": not bool(legacy_error),
                "canonical_success": False,
                "legacy_error": legacy_error,
                "canonical_error": canonical_error,
                "canonical_error_type": canonical_error,
                "action_diverged": False,
                "veto_diverged": False,
                "score_diverged": False,
                "pit_diverged": False,
                "divergence_categories_json": categories,
                "divergence_reason": (
                    ";".join(categories)
                    or ("LEGACY_ERROR" if legacy_error else "NONE")
                ),
                "score_bucket": formal_score_bucket(legacy_score),
                "research_only": True,
                "affects_legacy_output": False,
                "creates_order": False,
                "creates_trade": False,
                "creates_trade_plan": False,
                "creates_decision_packet": False,
            }
        )

    def stats(
        self,
        *,
        record_source: ShadowRecordSource | str = ShadowRecordSource.REAL,
    ) -> dict[str, Any]:
        source = ShadowRecordSource(record_source).value
        with get_connection(read_only=True) as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT CAST(observed_at AS DATE)),
                       SUM(CASE WHEN canonical_success THEN 1 ELSE 0 END),
                       SUM(CASE WHEN legacy_success THEN 1 ELSE 0 END),
                       SUM(CASE WHEN NOT canonical_success THEN 1 ELSE 0 END),
                       SUM(CASE WHEN NOT legacy_success THEN 1 ELSE 0 END),
                       SUM(CASE WHEN veto_input_status = 'VETO_INPUT_COMPLETE'
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN score_source_status = 'PIT_FACTOR_BUNDLE_MATCH'
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN score_source_status = 'SCORE_SOURCE_MISMATCH'
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN action_diverged THEN 1 ELSE 0 END),
                       SUM(CASE WHEN veto_diverged THEN 1 ELSE 0 END),
                       SUM(CASE WHEN pit_diverged THEN 1 ELSE 0 END),
                       SUM(CASE WHEN canonical_pit = TRUE THEN 1 ELSE 0 END),
                       AVG(shadow_overhead_ms), AVG(latency_legacy_ms),
                       QUANTILE_CONT(shadow_overhead_ms, 0.5),
                       QUANTILE_CONT(shadow_overhead_ms, 0.95),
                       SUM(CASE WHEN divergence_reason LIKE '%UNKNOWN_DIVERGENCE%'
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN score_source_status = 'G_GUARD_FORCE_WAIT'
                                THEN 1 ELSE 0 END)
                FROM canonical_shadow_observations_v2
                WHERE record_source = ?
                """,
                [source],
            ).fetchone()
            buckets = connection.execute(
                """
                SELECT score_bucket, COUNT(*),
                       SUM(CASE WHEN action_diverged THEN 1 ELSE 0 END),
                       STRING_AGG(DISTINCT legacy_action, ',' ORDER BY legacy_action),
                       STRING_AGG(DISTINCT canonical_action, ',' ORDER BY canonical_action),
                       STRING_AGG(DISTINCT divergence_reason, ';' ORDER BY divergence_reason)
                FROM canonical_shadow_observations_v2
                WHERE record_source = ?
                GROUP BY score_bucket
                ORDER BY score_bucket
                """,
                [source],
            ).fetchall()
            categories = connection.execute(
                """
                SELECT replace(CAST(item.value AS VARCHAR), chr(34), '') AS category,
                       COUNT(*)
                FROM canonical_shadow_observations_v2,
                     json_each(divergence_categories_json) AS item
                WHERE record_source = ?
                GROUP BY category
                ORDER BY category
                """,
                [source],
            ).fetchall()
            contexts = {
                name: {
                    row[0]: int(row[1])
                    for row in connection.execute(
                        f"""
                        SELECT {column}, COUNT(*)
                        FROM canonical_shadow_observations_v2
                        WHERE record_source = ?
                        GROUP BY {column}
                        ORDER BY {column}
                        """,
                        [source],
                    ).fetchall()
                }
                for name, column in (
                    ("user_context_sources", "user_context_source"),
                    ("portfolio_context_sources", "account_context_source"),
                    ("veto_input_statuses", "veto_input_status"),
                    ("pit_statuses", "canonical_pit"),
                )
            }
        total = int(totals[0] or 0)

        def _rate(value: int) -> float:
            return value / total if total else 0.0

        canonical_success = int(totals[2] or 0)
        canonical_failures = int(totals[4] or 0)
        veto_complete = int(totals[6] or 0)
        return {
            "record_source": source,
            "total": total,
            "trading_days": int(totals[1] or 0),
            "canonical_success": canonical_success,
            "canonical_failures": canonical_failures,
            "canonical_errors": canonical_failures,
            "canonical_error_rate": _rate(canonical_failures),
            "canonical_success_rate": _rate(canonical_success),
            "legacy_success": int(totals[3] or 0),
            "legacy_failures": int(totals[5] or 0),
            "legacy_success_rate": _rate(int(totals[3] or 0)),
            "veto_input_complete": veto_complete,
            "veto_input_complete_rate": _rate(veto_complete),
            "veto_input_incomplete": total - veto_complete,
            "score_source_complete": int(totals[7] or 0),
            "score_source_complete_rate": _rate(int(totals[7] or 0)),
            "score_source_mismatch": int(totals[8] or 0),
            "action_diverged": int(totals[9] or 0),
            "action_divergence_rate": _rate(int(totals[9] or 0)),
            "veto_diverged": int(totals[10] or 0),
            "pit_diverged": int(totals[11] or 0),
            "pit_complete": int(totals[12] or 0),
            "pit_complete_rate": _rate(int(totals[12] or 0)),
            "average_shadow_overhead_ms": float(totals[13] or 0.0),
            "average_canonical_latency_ms": float(totals[13] or 0.0),
            "average_legacy_latency_ms": float(totals[14] or 0.0),
            "p50_shadow_overhead_ms": float(totals[15] or 0.0),
            "p95_shadow_overhead_ms": float(totals[16] or 0.0),
            "unknown_divergence": int(totals[17] or 0),
            "score_source_g_guard_force_wait": int(totals[18] or 0),
            "score_buckets": {
                row[0]: {
                    "total": int(row[1]),
                    "action_diverged": int(row[2] or 0),
                    "action_divergence_rate": (
                        int(row[2] or 0) / int(row[1]) if row[1] else 0.0
                    ),
                    "legacy_actions": row[3] or "",
                    "canonical_actions": row[4] or "",
                    "reasons": row[5] or "",
                }
                for row in buckets
            },
            "divergence_categories": {
                row[0]: int(row[1]) for row in categories
            },
            **contexts,
        }


class CanonicalDecisionShadowService:
    def __init__(
        self,
        *,
        position_reader: ManualPositionManager | None = None,
        engine: DecisionEngine | None = None,
        repository: CanonicalDecisionShadowRepository | None = None,
    ) -> None:
        self.position_reader = position_reader or ManualPositionManager()
        self.builder = CanonicalDecisionInputBuilder(self.position_reader)
        self.engine = engine or DecisionEngine(
            permission_service=_NoLivePermission(),
            manual_positions=self.position_reader,
            plan_repo=_NoTradePlanRepository(),
        )
        self.repository = repository or CanonicalDecisionShadowRepository()

    def run_and_record(
        self,
        *,
        request_id: str,
        user_context: UserContext,
        request: DecisionShadowRequest,
        bundle: FactorBundle,
        market_bars: list[MarketBar],
        legacy_result: FormalResult,
        veto_context: CanonicalVetoContext,
        veto_event_count: int,
        legacy_latency_ms: float,
        canonical_started_at: float | None = None,
        portfolio_context: PortfolioContext | None = None,
        record_source: ShadowRecordSource | str = ShadowRecordSource.TEST,
    ) -> CanonicalShadowRun:
        started_at = canonical_started_at or time.perf_counter()
        if portfolio_context is None:
            holding = self.position_reader.get_holding(
                user_context.local_user_id, request.symbol
            )
            holdings = (holding,) if holding is not None else ()
            portfolio_context = PortfolioContext.research_default(
                local_user_id=user_context.local_user_id,
                as_of=request.data_cutoff,
                holdings=holdings,
            )
        built = self.builder.build(
            request_id=request_id,
            user_context=user_context,
            request=request,
            bundle=bundle,
            market_bars=market_bars,
            legacy_result=legacy_result,
            portfolio_context=portfolio_context,
            veto_context=veto_context,
        )
        response = self.engine.decide(built.decision_input)
        latency_canonical_ms = max(
            0.0, (time.perf_counter() - started_at) * 1000.0
        )
        legacy_action = legacy_result.final_action.value.upper()
        canonical_action = response.action.value
        legacy_veto = bool(legacy_result.hard_veto)
        canonical_veto = bool(response.veto_triggered)
        score_diverged = abs(legacy_result.score - response.formal_score) > 1e-12
        categories = classify_divergence(
            legacy_score=legacy_result.score,
            canonical_score=response.formal_score,
            legacy_action=legacy_action,
            canonical_action=canonical_action,
            legacy_veto=legacy_veto,
            canonical_veto=canonical_veto,
            score_source_mismatch=built.score_source_mismatch,
            canonical_pit=built.decision_input.point_in_time_ok,
            canonical_data_status=response.data_status.value,
            veto_input_complete=built.veto_input_complete,
            canonical_current_position=response.current_position_ratio,
            account_context_assumption=(
                response.account_context_is_research_assumption
            ),
            score_diverged=score_diverged,
            pit_diverged=not built.decision_input.point_in_time_ok,
        )
        self.repository.save(
            {
                "observation_id": f"cds2_{uuid.uuid4().hex}",
                "request_id": request_id,
                "record_source": ShadowRecordSource(record_source).value,
                "symbol": request.symbol,
                "local_user_id": user_context.local_user_id,
                "external_user_id": user_context.external_user_id,
                "channel": user_context.channel,
                "user_context_source": user_context.source,
                "user_authenticated": user_context.authenticated,
                "observed_at": datetime.now().astimezone(),
                "data_cutoff": request.data_cutoff,
                "snapshot_id": response.snapshot_id,
                "legacy_score": legacy_result.score,
                "canonical_score": response.formal_score,
                "legacy_technical_score": bundle.technical.score if bundle.technical else None,
                "legacy_fundamental_score": (
                    bundle.fundamental.score if bundle.fundamental else None
                ),
                "canonical_technical_score": built.decision_input.technical_score,
                "canonical_fundamental_score": (
                    built.decision_input.fundamental_score
                ),
                "legacy_action": legacy_action,
                "canonical_action": canonical_action,
                "legacy_veto": legacy_veto,
                "canonical_veto": canonical_veto,
                "canonical_pit": built.decision_input.point_in_time_ok,
                "canonical_data_status": response.data_status.value,
                "score_source_status": (
                    "G_GUARD_FORCE_WAIT"
                    if built.score_source == LEGACY_SOURCE
                    else (
                        "SCORE_SOURCE_MISMATCH"
                        if built.score_source_mismatch
                        else "PIT_FACTOR_BUNDLE_MATCH"
                    )
                ),
                "veto_input_status": (
                    "VETO_INPUT_COMPLETE"
                    if built.veto_input_complete
                    else "VETO_INPUT_INCOMPLETE"
                ),
                "veto_source": built.veto_source,
                "veto_reason": built.veto_reason,
                "veto_event_count": veto_event_count,
                "account_context_source": response.account_context_source,
                "canonical_current_position": response.current_position_ratio,
                "canonical_target_position": response.target_position_ratio,
                "canonical_price_zones_json": {
                    "entry_zone": response.frozen_entry_zone,
                    "preferred_zone": response.frozen_preferred_zone,
                    "stop_loss": response.frozen_stop_loss_price,
                    "take_profit_zone": response.price_zones.take_profit_zone,
                },
                "canonical_execution_status": response.execution_status,
                "latency_legacy_ms": legacy_latency_ms,
                "latency_canonical_ms": latency_canonical_ms,
                "shadow_overhead_ms": latency_canonical_ms,
                "legacy_success": True,
                "canonical_success": True,
                "legacy_error": "",
                "canonical_error": "",
                "canonical_error_type": "",
                "action_diverged": legacy_action != canonical_action,
                "veto_diverged": legacy_veto != canonical_veto,
                "score_diverged": score_diverged,
                "pit_diverged": not built.decision_input.point_in_time_ok,
                "divergence_categories_json": list(categories),
                "divergence_reason": ";".join(categories) or "NONE",
                "score_bucket": formal_score_bucket(response.formal_score),
                "research_only": True,
                "affects_legacy_output": False,
                "creates_order": False,
                "creates_trade": False,
                "creates_trade_plan": False,
                "creates_decision_packet": False,
            }
        )
        return CanonicalShadowRun(
            response=response,
            score_source_mismatch=built.score_source_mismatch,
            score_source_reason=built.score_source_reason,
            veto_source=built.veto_source,
            veto_reason=built.veto_reason,
            veto_input_complete=built.veto_input_complete,
            divergence_categories=categories,
            latency_canonical_ms=latency_canonical_ms,
        )


__all__ = [
    "CanonicalDecisionShadowRepository",
    "CanonicalDecisionShadowService",
    "CanonicalShadowRun",
    "classify_divergence",
    "formal_score_bucket",
    "runtime_shadow_record_source",
]
