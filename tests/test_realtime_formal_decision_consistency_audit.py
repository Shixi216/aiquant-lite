"""Deterministic audit of the current realtime formal core vs DecisionEngine.

These are characterization tests, not equality tests.  They deliberately expose
the P0 boundary: orchestration.formal_result stops before the production
DecisionEngine semantics used by historical formal replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.account_context import RESEARCH_DEFAULT_ACCOUNT_CONTEXT
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.execution_status import compute_execution_status
from trading.decision_support.manual_position_manager import Holding
from trading.decision_support.price_zones import Bar
from trading.decision_support.task_context import TaskContext
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.schemas import DecisionShadowRequest


CUTOFF = datetime(2026, 8, 10, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
FORMAL_ONLY_MISSING_FIELDS = {
    "action_strength",
    "allow_new_position",
    "allow_add_position",
    "recommend_reduce",
    "recommend_exit",
    "target_position_ratio",
    "position_change_ratio",
    "recommended_batches",
    "entry_zone",
    "preferred_zone",
    "stop_loss_price",
    "take_profit_zone",
    "execution_status",
    "execution_status_reason",
    "next_action",
}


class _NoLivePermission:
    @staticmethod
    def can_live_trade(local_user_id: str) -> bool:
        return False


class _NoPlanRepository:
    @staticmethod
    def create_plan(plan: object) -> None:
        raise AssertionError("audit must remain read-only")


class _PositionReader:
    def __init__(self, holding: Holding | None) -> None:
        self.holding = holding

    def get_holding(self, local_user_id: str, symbol: str) -> Holding | None:
        return self.holding


@dataclass(frozen=True)
class AuditCase:
    name: str
    technical_score: float
    fundamental_score: float
    confidence: float
    expected_formal_action: str
    expected_engine_action: str
    data_status: DataStatus = DataStatus.FRESH
    veto: bool = False
    holding_ratio: float = 0.0
    average_cost: float = 0.0
    current_price: float = 100.0
    capital_score: float = 0.0
    evaluation_price: float | None = None
    expected_evaluation_status: str | None = None
    missing_data: tuple[str, ...] = ()
    engine_error: type[Exception] | None = None


CASES = (
    AuditCase("high_no_position_fresh", 0.8, 0.8, 0.8, "BUY", "STRONG_BUY"),
    AuditCase("medium_low_confidence", 0.5, 0.5, 0.4, "BUY", "STRONG_BUY"),
    AuditCase("high_but_veto", 0.9, 0.9, 0.9, "VETO", "AVOID", veto=True),
    AuditCase(
        "stale_data",
        0.9,
        0.9,
        0.9,
        "BUY",
        "WAIT",
        data_status=DataStatus.STALE,
    ),
    AuditCase(
        "existing_position_add",
        0.8,
        0.8,
        0.8,
        "BUY",
        "ADD",
        holding_ratio=0.10,
        average_cost=90.0,
        capital_score=0.2,
    ),
    AuditCase(
        "existing_position_negative_exit",
        -0.8,
        -0.8,
        0.8,
        "SELL",
        "EXIT",
        holding_ratio=0.10,
        average_cost=100.0,
        current_price=90.0,

    ),
    AuditCase(
        "price_in_preferred_zone",
        0.8,
        0.8,
        0.8,
        "BUY",
        "STRONG_BUY",
        evaluation_price=99.0,
        expected_evaluation_status="立即执行",
    ),
    AuditCase(
        "price_above_entry_zone",
        0.8,
        0.8,
        0.8,
        "BUY",
        "STRONG_BUY",
        evaluation_price=102.0,
        expected_evaluation_status="禁止追高",
    ),
    AuditCase(
        "stop_loss_triggered",
        0.8,
        0.8,
        0.8,
        "BUY",
        "STRONG_BUY",
        evaluation_price=90.0,
        expected_evaluation_status="信号失效",
    ),
    AuditCase(
        "fundamental_missing_pit_not_propagated",
        0.8,
        0.0,
        0.52,
        "BUY",
        "STRONG_BUY",
        data_status=DataStatus.DEGRADED,
        missing_data=("PIT_FUNDAMENTAL_MISSING",),
    ),
)


def _bars() -> list[Bar]:
    start = date(2026, 5, 1)
    return [
        Bar(
            trade_date=start + timedelta(days=index),
            open=100.0,
            high=102.0,
            low=98.0,
            close=100.0,
            volume=1_000_000.0,
        )
        for index in range(60)
    ]


def _holding(case: AuditCase) -> Holding | None:
    if case.holding_ratio <= 0:
        return None
    return Holding(
        local_user_id="audit-user",
        symbol="600000.SH",
        quantity=1_000.0,
        average_cost=case.average_cost,
        position_ratio=case.holding_ratio,
        total_assets=100_000.0,
    )


def _formal(case: AuditCase):
    return formal_result(
        DecisionShadowRequest(
            symbol="600000.SH",
            data_cutoff=CUTOFF,
            technical_score=case.technical_score,
            technical_confidence=case.confidence,
            fundamental_score=case.fundamental_score,
            fundamental_confidence=case.confidence,
            hard_veto=case.veto,
            persist_shadow=False,
        )
    )


def _engine(case: AuditCase):
    holding = _holding(case)
    engine = DecisionEngine(
        permission_service=_NoLivePermission(),
        plan_repo=_NoPlanRepository(),
        manual_positions=_PositionReader(holding),
    )
    score = case.technical_score * 0.60 + case.fundamental_score * 0.40
    response = engine.decide(
        DecisionInput(
            task=TaskContext(
                local_user_id="audit-user",
                external_user_id="audit-user",
                channel="test",
                mode="DECISION",
                symbol="600000.SH",
                snapshot_id=f"audit-{case.name}",
                trade_date="2026-08-10",
                snapshot_time=CUTOFF,
                collected_at=CUTOFF,
                data_cutoff=CUTOFF,
            ),
            formal_score=score,
            technical_score=case.technical_score,
            fundamental_score=case.fundamental_score,
            point_in_time_ok=True,
            account_context=RESEARCH_DEFAULT_ACCOUNT_CONTEXT,
            capital_score=case.capital_score,
            confidence=case.confidence,
            data_status=case.data_status,
            veto=(
                VetoResult(veto_triggered=True, veto_type="AUDIT_VETO")
                if case.veto
                else None
            ),
            current_price=case.current_price,
            bars=_bars(),
            coverage_ratio=0.0 if case.missing_data else 1.0,
            missing_data=list(case.missing_data),
        )
    )
    return response


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_current_realtime_formal_core_is_not_decision_engine_equivalent(
    case: AuditCase,
) -> None:
    realtime = _formal(case)
    expected_score = case.technical_score * 0.60 + case.fundamental_score * 0.40
    if case.engine_error is not None:
        assert realtime.score == pytest.approx(expected_score)
        assert realtime.final_action.value.upper() == case.expected_formal_action
        with pytest.raises(case.engine_error, match="NoneType"):
            _engine(case)
        return
    historical_core = _engine(case)

    # The only fully shared semantics are formal 60/40 score and confidence.
    assert realtime.score == pytest.approx(expected_score)
    assert historical_core.formal_score == pytest.approx(expected_score)
    assert realtime.confidence == pytest.approx(case.confidence)
    assert historical_core.confidence == pytest.approx(case.confidence)

    realtime_action = realtime.final_action.value.upper()
    engine_action = historical_core.action.value
    assert realtime_action == case.expected_formal_action
    assert engine_action == case.expected_engine_action
    assert realtime_action != engine_action

    # FormalResult cannot represent the required downstream decision fields.
    realtime_payload = realtime.model_dump(mode="json")
    assert FORMAL_ONLY_MISSING_FIELDS.isdisjoint(realtime_payload)
    engine_payload = historical_core.to_dict()
    assert FORMAL_ONLY_MISSING_FIELDS.issubset(
        {
            *engine_payload,
            *engine_payload["price_zones"],
        }
    )

    if case.evaluation_price is not None:
        status, _ = compute_execution_status(
            current_price=case.evaluation_price,
            action=engine_action,
            veto_triggered=historical_core.veto_triggered,
            frozen_entry_zone=historical_core.frozen_entry_zone,
            frozen_preferred_zone=historical_core.frozen_preferred_zone,
            frozen_stop_loss_price=historical_core.frozen_stop_loss_price,
        )
        assert status == case.expected_evaluation_status
        assert "execution_status" not in realtime_payload

    if case.missing_data:
        # DecisionInput has no PIT boolean; DecisionEngine currently hard-codes it true.
        # v2: DEGRADED 不改变动作方向，0.48 ≥ P90 回退 0.42 → STRONG_BUY
        assert historical_core.action.value == "STRONG_BUY"
        assert historical_core.missing_data == list(case.missing_data)
