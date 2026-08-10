from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from trading.decision_support.action import Action
from trading.decision_support.decision_response import DecisionResponse, PriceZone
from trading.review.decision_snapshots import (
    SUPPORTED_SNAPSHOT_STAGES,
    DecisionSnapshotRepository,
    DecisionSnapshotService,
    SnapshotStage,
)


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 7, 9, 35, tzinfo=TZ)
FACTORS = {
    "technical": "positive",
    "fundamental": "neutral",
    "sentiment": "neutral",
    "policy_news": "neutral",
    "capital_flow": "negative",
}


def _response() -> DecisionResponse:
    return DecisionResponse(
        action=Action.BUY,
        formal_score=0.656,
        current_position_ratio=0,
        target_position_ratio=0.12,
        recommended_batches=3,
        price_zones=PriceZone(
            entry_zone=[21.07, 22.40],
            preferred_zone=[21.07, 21.60],
            take_profit_zone=[24.0, 25.0],
            stop_loss_price=20.50,
        ),
        frozen_entry_zone=[21.07, 22.40],
        frozen_preferred_zone=[21.07, 21.60],
        frozen_stop_loss_price=20.50,
        execution_status="untrusted report value",
    )


@pytest.fixture
def repository(tmp_path):
    return DecisionSnapshotRepository(tmp_path / "snapshots.duckdb")


def test_exactly_seven_required_stages_are_registered():
    assert [item.value for item in SUPPORTED_SNAPSHOT_STAGES] == [
        "PRE_MARKET",
        "POST_AUCTION",
        "OPEN_5_MIN",
        "TEN_OCLOCK",
        "MIDDAY",
        "FOURTEEN_THIRTY",
        "CLOSE",
    ]


def test_snapshot_contains_required_fields_and_verified_hash(repository):
    snapshot = DecisionSnapshotService(repository).capture(
        decision_id="decision-1",
        decision_version=1,
        symbol="600000.SH",
        stage=SnapshotStage.OPEN_5_MIN,
        response=_response(),
        five_factor_conclusions=FACTORS,
        current_price=21.50,
        captured_at=NOW,
        data_time=NOW,
        data_hash="a" * 64,
    )
    assert snapshot.formal_action == "BUY"
    assert snapshot.execution_status == "立即执行"
    assert snapshot.formal_score == 0.656
    assert snapshot.target_position_ratio == 0.12
    assert snapshot.frozen_entry_zone == (21.07, 22.40)
    assert snapshot.frozen_stop_loss_price == 20.50
    assert snapshot.veto_triggered is False
    assert snapshot.change_reason == "INITIAL_SNAPSHOT"
    assert snapshot.verify_hash() is True


def test_execution_status_is_recomputed_from_frozen_zone(repository):
    snapshot = DecisionSnapshotService(repository).capture(
        decision_id="decision-2",
        decision_version=1,
        symbol="600000.SH",
        stage=SnapshotStage.TEN_OCLOCK,
        response=_response(),
        five_factor_conclusions=FACTORS,
        current_price=22.20,
        captured_at=NOW,
        data_time=NOW,
        data_hash="b" * 64,
    )
    assert snapshot.execution_status == "等待回调"
    assert snapshot.execution_status != "untrusted report value"


def test_old_snapshot_is_append_only_and_never_overwritten(repository):
    service = DecisionSnapshotService(repository)
    first = service.capture(
        decision_id="decision-3",
        decision_version=1,
        symbol="600000.SH",
        stage=SnapshotStage.PRE_MARKET,
        response=_response(),
        five_factor_conclusions=FACTORS,
        current_price=21.50,
        captured_at=NOW,
        data_time=NOW,
        data_hash="c" * 64,
    )
    second = service.capture(
        decision_id="decision-3",
        decision_version=1,
        symbol="600000.SH",
        stage=SnapshotStage.POST_AUCTION,
        response=_response(),
        five_factor_conclusions=FACTORS,
        current_price=22.20,
        captured_at=NOW + timedelta(minutes=1),
        data_time=NOW + timedelta(minutes=1),
        data_hash="d" * 64,
    )
    assert [item.snapshot_id for item in repository.list_for_decision("decision-3")] == [
        first.snapshot_id,
        second.snapshot_id,
    ]
    assert "EXECUTION:" in second.change_reason
    assert "DATA_HASH_CHANGED" in second.change_reason
    with pytest.raises(duckdb.ConstraintException):
        repository.save(first)
    with pytest.raises(FrozenInstanceError):
        first.formal_score = 0


def test_snapshot_module_has_no_order_or_position_write_api():
    assert not hasattr(DecisionSnapshotService, "submit_order")
    assert not hasattr(DecisionSnapshotService, "update_position")
