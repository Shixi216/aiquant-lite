from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from database.migrations.v0116_overheat_preproduction_shadow import (
    MIGRATION_ID,
    apply_migration,
)
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.research.overheat_preproduction_shadow import (
    FIXED_CALIBRATION,
    OverheatShadowRepository,
    PreproductionOverheatShadowService,
    PricePoint,
    calculate_overheat_features,
)


TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2026, 8, 7, 15, 30, tzinfo=TZ)


def _points(count: int = 65, *, accelerating: bool = True) -> list[PricePoint]:
    values = []
    for index in range(count):
        close = 10.0 + index * (0.18 if accelerating else 0.01)
        values.append(
            PricePoint(
                bar_id=f"bar_{index}",
                event_time=CUTOFF - timedelta(days=count - index - 1),
                data_available_time=CUTOFF - timedelta(days=count - index - 1),
                close=close,
                high=close * 1.01,
                low=close * 0.99,
            )
        )
    return values


class FakeRepository:
    def __init__(self, points: list[PricePoint]) -> None:
        self.points = points
        self.observations: list[dict] = []
        self.labels: list[dict] = []
        self.pending: list[tuple] = []
        self.future: list[PricePoint] = []

    def recent_points(self, symbol, data_cutoff, *, limit=65):
        return self.points[-limit:]

    def save_observation(self, values):
        self.observations.append(dict(values))

    def pending_observations(self, as_of, *, limit=100):
        return self.pending[:limit]

    def future_points(self, symbol, data_cutoff, as_of):
        return self.future

    def save_label(self, **values):
        self.labels.append(values)


def _request(**updates) -> DecisionShadowRequest:
    values = {
        "symbol": "600000.SH",
        "data_cutoff": CUTOFF,
        "technical_score": 0.85,
        "technical_confidence": 0.8,
        "fundamental_score": 0.25,
        "fundamental_confidence": 0.7,
        "hard_veto": False,
        "persist_shadow": False,
    }
    values.update(updates)
    return DecisionShadowRequest(**values)


def test_migration_is_idempotent_and_tables_are_research_only() -> None:
    connection = duckdb.connect(":memory:")
    assert apply_migration(connection) is True
    assert apply_migration(connection) is False
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
        [MIGRATION_ID],
    ).fetchone()[0] == 1
    names = {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_name LIKE 'overheat_shadow%'
            """
        ).fetchall()
    }
    assert names == {
        "overheat_shadow_observations",
        "overheat_shadow_forward_labels",
    }


def test_fixed_calibration_matches_frozen_ab_research() -> None:
    assert FIXED_CALIBRATION.expansion.q75 == pytest.approx(
        0.20292835976991452
    )
    assert FIXED_CALIBRATION.ma60_slope.q75 == pytest.approx(
        0.048701541558149986
    )
    assert FIXED_CALIBRATION.maximum_component_multiplier == 2.0


def test_features_use_only_65_points_available_at_cutoff() -> None:
    result = calculate_overheat_features(_points())
    assert result.status == "AVAILABLE"
    assert result.source_bar_id == "bar_64"
    assert result.ma20_ma60_expansion is not None
    assert result.ma60_slope_5 is not None
    assert result.ma20_ma60_expansion > 0
    assert result.ma60_slope_5 > 0


def test_short_history_is_neutral_and_never_blocks_a() -> None:
    repository = FakeRepository(_points(30))
    service = PreproductionOverheatShadowService(
        repository, clock=lambda: CUTOFF,
    )
    request = _request()
    official_before = formal_result(request).model_dump(mode="json")
    service.record(request, formal_result(request), formal_result)
    row = repository.observations[0]
    assert row["feature_status"] == "INSUFFICIENT_HISTORY"
    assert row["total_penalty"] == 0
    assert row["shadow_technical_score"] == request.technical_score
    assert formal_result(request).model_dump(mode="json") == official_before


def test_shadow_record_is_independent_and_official_a_is_unchanged() -> None:
    repository = FakeRepository(_points())
    service = PreproductionOverheatShadowService(
        repository, clock=lambda: CUTOFF,
    )
    request = _request()
    official = formal_result(request)
    official_before = official.model_dump(mode="json")
    service.record(request, official, formal_result)
    row = repository.observations[0]
    assert row["original_technical_score"] == request.technical_score
    assert row["shadow_technical_score"] <= request.technical_score
    assert row["a_formal_score"] == official.score
    assert row["b_shadow_formal_score"] <= official.score
    assert formal_result(request).model_dump(mode="json") == official_before


def test_hard_veto_is_identical_in_a_and_b() -> None:
    repository = FakeRepository(_points())
    service = PreproductionOverheatShadowService(
        repository, clock=lambda: CUTOFF,
    )
    request = _request(hard_veto=True)
    service.record(request, formal_result(request), formal_result)
    row = repository.observations[0]
    assert row["a_action"] == "veto"
    assert row["b_shadow_action"] == "veto"
    assert row["action_diverged"] is False


def test_matured_labels_are_written_for_each_available_horizon() -> None:
    repository = FakeRepository([])
    repository.pending = [("obs_1", "600000.SH", CUTOFF, 10.0)]
    repository.future = _points(10, accelerating=False)
    service = PreproductionOverheatShadowService(
        repository, clock=lambda: CUTOFF + timedelta(days=30),
    )
    assert service.update_matured_outcomes(
        as_of=CUTOFF + timedelta(days=30)
    ) == 4
    assert [item["horizon"] for item in repository.labels] == [1, 3, 5, 10]


def test_repository_targets_only_shadow_tables() -> None:
    names = set(OverheatShadowRepository.save_observation.__code__.co_consts)
    joined = " ".join(item for item in names if isinstance(item, str))
    assert "overheat_shadow_observations" in joined
    assert "orders" not in joined
    assert "trades" not in joined
    assert "manual_positions" not in joined
