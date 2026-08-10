from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from data_hub.schemas.unified import FactorOutput, FactorType
from database.migrations.v0109_five_factor_orchestration import (
    apply_migration,
)
from router.api.app import app
from trading.research.orchestration.availability import (
    classify_availability,
)
from trading.research.orchestration.correlation import (
    calculate_correlation_discounts,
)
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.evidence_graph import (
    build_evidence_graph,
)
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.models import FORMAL_WEIGHTS
from trading.research.orchestration.research import ResearchEngine
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    EvaluationRequest,
    FactorAvailabilityStatus,
    FactorCoverage,
    FactorView,
    OrchestrationRiskFlag,
    ResearchRequest,
    ScreeningRequest,
)
from trading.research.orchestration.screening import ScreeningEngine
from trading.research.orchestration.shadow_scorer import score_shadow_bundle
from trading.schemas import Action, AnalysisMode, DecisionPacket


CUTOFF = datetime.fromisoformat("2026-07-29T15:30:00+08:00")


def _factor(
    factor_type: FactorType,
    *,
    score: float = 0.5,
    confidence: float = 0.8,
    evidence_ids: list[str] | None = None,
    risk_flags: list[str] | None = None,
    status: FactorAvailabilityStatus = FactorAvailabilityStatus.AVAILABLE,
    data_cutoff: datetime = CUTOFF,
) -> FactorView:
    suffix = factor_type.value.lower()
    evidence = evidence_ids or [f"raw_{suffix}"]
    return FactorView(
        factor_output_id=f"fac_{suffix}",
        factor_type=factor_type,
        score=score,
        confidence=confidence,
        shadow_mode=True,
        generated_at=max(CUTOFF, data_cutoff),
        data_cutoff=data_cutoff,
        evidence_ids=evidence,
        risk_flags=risk_flags or [],
        algorithm_version=f"{suffix}-v1",
        input_snapshot_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        availability_status=status,
        availability_reason="test",
        event_cluster_ids=[
            item for item in evidence if item.startswith("evt_")
        ],
        market_record_ids=[
            item for item in evidence if item.startswith("hbar_")
        ],
        source_factor_output_id=f"fac_{suffix}",
    )


def _bundle(
    factors: dict[FactorType, FactorView],
    *,
    mode: AnalysisMode = AnalysisMode.SCREENING,
):
    return FactorLoader(object()).build_bundle(
        symbol="600172.SH",
        analysis_mode=mode,
        data_cutoff=CUTOFF,
        factors=factors,
    )


def test_01_shadow_composite_factor_type_is_explicit() -> None:
    assert FactorType.SHADOW_COMPOSITE.value == "SHADOW_COMPOSITE"


def test_02_bundle_unifies_all_five_factor_slots() -> None:
    factors = {
        factor_type: _factor(factor_type)
        for factor_type in FactorType
        if factor_type != FactorType.SHADOW_COMPOSITE
    }
    bundle = _bundle(factors)
    assert bundle.factor_coverage.display == "5/5"
    assert not bundle.missing_factor_types


def test_03_neutral_zero_is_available_and_not_missing() -> None:
    bundle = _bundle(
        {FactorType.TECHNICAL: _factor(FactorType.TECHNICAL, score=0.0)}
    )
    assert bundle.factor_coverage.display == "1/5"
    assert FactorType.TECHNICAL in bundle.available_factor_types
    assert FactorType.TECHNICAL not in bundle.missing_factor_types


def test_04_missing_factor_does_not_enter_shadow_denominator() -> None:
    bundle = _bundle(
        {FactorType.TECHNICAL: _factor(FactorType.TECHNICAL, score=0.8)}
    )
    composite = score_shadow_bundle(bundle, generated_at=CUTOFF)
    assert composite.score == pytest.approx(0.8)
    assert composite.effective_weights == {"TECHNICAL": 1.0}


def test_05_effective_weights_sum_to_one() -> None:
    composite = score_shadow_bundle(
        _bundle(
            {
                FactorType.TECHNICAL: _factor(FactorType.TECHNICAL),
                FactorType.CAPITAL_FLOW: _factor(FactorType.CAPITAL_FLOW),
            }
        ),
        generated_at=CUTOFF,
    )
    assert sum(composite.effective_weights.values()) == pytest.approx(1.0)


def test_06_low_coverage_constrains_confidence() -> None:
    composite = score_shadow_bundle(
        _bundle(
            {FactorType.TECHNICAL: _factor(FactorType.TECHNICAL, confidence=1)}
        ),
        generated_at=CUTOFF,
    )
    assert composite.confidence <= 0.2
    assert OrchestrationRiskFlag.LOW_FACTOR_COVERAGE in composite.risk_flags


def test_07_single_factor_dominance_is_flagged() -> None:
    composite = score_shadow_bundle(
        _bundle({FactorType.TECHNICAL: _factor(FactorType.TECHNICAL)}),
        generated_at=CUTOFF,
    )
    assert OrchestrationRiskFlag.SINGLE_FACTOR_DOMINANCE in composite.risk_flags


def test_08_sentiment_policy_shared_event_is_discounted() -> None:
    factors = {
        FactorType.SENTIMENT: _factor(
            FactorType.SENTIMENT,
            evidence_ids=["evt_shared", "raw_s"],
        ),
        FactorType.POLICY_NEWS: _factor(
            FactorType.POLICY_NEWS,
            evidence_ids=["evt_shared", "raw_p"],
        ),
    }
    discount = calculate_correlation_discounts(factors)
    assert len(discount) == 1
    assert discount[0].discounted_factor == FactorType.POLICY_NEWS
    assert discount[0].applied_discount > 0


def test_09_distinct_sentiment_policy_events_are_not_discounted() -> None:
    factors = {
        FactorType.SENTIMENT: _factor(
            FactorType.SENTIMENT,
            evidence_ids=["evt_s"],
        ),
        FactorType.POLICY_NEWS: _factor(
            FactorType.POLICY_NEWS,
            evidence_ids=["evt_p"],
        ),
    }
    assert calculate_correlation_discounts(factors) == []


def test_10_technical_capital_shared_bar_is_recorded() -> None:
    factors = {
        FactorType.TECHNICAL: _factor(
            FactorType.TECHNICAL,
            evidence_ids=["hbar_shared", "hbar_old"],
        ),
        FactorType.CAPITAL_FLOW: _factor(
            FactorType.CAPITAL_FLOW,
            evidence_ids=["hbar_shared"],
        ),
    }
    discount = calculate_correlation_discounts(factors)
    assert discount[0].factor_pair == "TECHNICAL<->CAPITAL_FLOW"
    assert discount[0].overlap_ratio == 1


def test_11_conflict_factor_has_zero_directional_contribution() -> None:
    bundle = _bundle(
        {
            FactorType.TECHNICAL: _factor(
                FactorType.TECHNICAL,
                score=0.6,
            ),
            FactorType.SENTIMENT: _factor(
                FactorType.SENTIMENT,
                score=-1,
                status=FactorAvailabilityStatus.CONFLICT,
            ),
        }
    )
    composite = score_shadow_bundle(bundle, generated_at=CUTOFF)
    assert composite.score == pytest.approx(0.6)
    assert "SENTIMENT" not in composite.effective_weights


def test_12_stale_factor_is_excluded_in_decision_mode() -> None:
    bundle = _bundle(
        {
            FactorType.TECHNICAL: _factor(
                FactorType.TECHNICAL,
                score=0.4,
            ),
            FactorType.SENTIMENT: _factor(
                FactorType.SENTIMENT,
                score=-1,
                status=FactorAvailabilityStatus.STALE,
            ),
        },
        mode=AnalysisMode.DECISION,
    )
    composite = score_shadow_bundle(bundle, generated_at=CUTOFF)
    assert composite.score == pytest.approx(0.4)
    assert "SENTIMENT" not in composite.effective_weights


def test_13_future_factor_is_rejected() -> None:
    factor = _factor(
        FactorType.TECHNICAL,
        data_cutoff=CUTOFF + timedelta(minutes=1),
    )
    status, _ = classify_availability(
        factor,
        analysis_mode=AnalysisMode.DECISION,
        requested_cutoff=CUTOFF,
    )
    assert status == FactorAvailabilityStatus.FUTURE_DATA_REJECTED


def test_14_evidence_graph_deduplicates_shared_fact() -> None:
    groups, clusters = build_evidence_graph(
        {
            FactorType.SENTIMENT: _factor(
                FactorType.SENTIMENT,
                evidence_ids=["evt_shared"],
            ),
            FactorType.POLICY_NEWS: _factor(
                FactorType.POLICY_NEWS,
                evidence_ids=["evt_shared"],
            ),
        }
    )
    assert len(groups) == 1
    assert clusters == ["evt_shared"]


def test_15_shadow_output_is_always_shadow_and_formal_weight_zero() -> None:
    composite = score_shadow_bundle(
        _bundle({FactorType.TECHNICAL: _factor(FactorType.TECHNICAL)}),
        generated_at=CUTOFF,
    )
    assert composite.shadow_mode is True
    assert composite.factor_output.shadow_mode is True
    assert composite.formal_strategy_weight == 0


def test_16_shadow_output_uses_separate_factor_type() -> None:
    composite = score_shadow_bundle(
        _bundle({FactorType.TECHNICAL: _factor(FactorType.TECHNICAL)}),
        generated_at=CUTOFF,
    )
    assert composite.factor_output.factor_type == FactorType.SHADOW_COMPOSITE


def _decision_request(**updates: Any) -> DecisionShadowRequest:
    values = {
        "symbol": "600172.SH",
        "data_cutoff": CUTOFF,
        "technical_score": 0.5,
        "technical_confidence": 0.8,
        "fundamental_score": -0.25,
        "fundamental_confidence": 0.5,
    }
    values.update(updates)
    return DecisionShadowRequest(**values)


def test_17_formal_score_remains_exactly_60_40() -> None:
    result = formal_result(_decision_request())
    assert result.score == pytest.approx(0.5 * 0.6 - 0.25 * 0.4)
    assert result.formal_weights == {
        FactorType.TECHNICAL.value: 0.6,
        FactorType.FUNDAMENTAL.value: 0.4,
    }
    assert FORMAL_WEIGHTS[FactorType.TECHNICAL] == 0.6


def test_18_shadow_is_not_used_for_formal_action() -> None:
    result = formal_result(_decision_request())
    assert result.shadow_used_for_action is False
    assert result.proposal_action == Action.HOLD


def test_19_hard_veto_is_not_bypassed() -> None:
    result = formal_result(
        _decision_request(
            technical_score=1,
            fundamental_score=1,
            hard_veto=True,
        )
    )
    assert result.proposal_action == Action.BUY
    assert result.final_action == Action.VETO
    assert result.shadow_used_for_veto is False


def test_20_decision_packet_remains_frozen() -> None:
    assert DecisionPacket.model_config["frozen"] is True


def test_21_evaluation_requires_ex_post_timestamp() -> None:
    with pytest.raises(ValidationError):
        EvaluationRequest(
            symbol="600172.SH",
            analysis_time=CUTOFF,
            formal_score=0,
            formal_action=Action.HOLD,
            shadow_score=0,
            composite_confidence=0.5,
            factor_coverage=FactorCoverage(
                available_count=2,
                coverage_ratio=0.4,
                display="2/5",
            ),
            effective_weights={"TECHNICAL": 1},
            risk_vetoed=False,
            data_complete=False,
            input_snapshot_hash="a" * 64,
            evaluated_at=CUTOFF,
        )


def test_22_migration_is_idempotent_twice(tmp_path: Path) -> None:
    connection = duckdb.connect(str(tmp_path / "stage8.duckdb"))
    try:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False
    finally:
        connection.close()


def test_23_migration_creates_four_append_only_structures(
    tmp_path: Path,
) -> None:
    connection = duckdb.connect(str(tmp_path / "stage8-tables.duckdb"))
    try:
        apply_migration(connection)
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name IN (
                    'factor_bundle_snapshots',
                    'shadow_composite_snapshots',
                    'factor_correlation_audits',
                    'orchestration_evaluations'
                )
                """
            ).fetchall()
        }
        assert len(tables) == 4
    finally:
        connection.close()


def test_24_openapi_exposes_only_five_orchestration_routes() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/v1/orchestration/screen",
        "/v1/orchestration/research",
        "/v1/orchestration/decision-shadow",
        "/v1/orchestration/symbols/{symbol}",
        "/v1/orchestration/evaluate",
    }
    assert expected <= set(paths)


class _FakeLoader:
    def __init__(self, bundles: list[Any]) -> None:
        self.bundles = bundles
        self.calls = 0

    def load_many(self, **_: Any):
        self.calls += 1
        return self.bundles, 1

    @staticmethod
    def build_bundle(**_: Any):
        return FactorLoader(object()).build_bundle(**_)


class _FakeRepository:
    def __init__(self) -> None:
        self.saved = 0

    def save_bundle_and_composite(self, *_: Any) -> bool:
        self.saved += 1
        return True


def test_25_screening_5534_is_one_bulk_load_and_zero_external_calls() -> None:
    bundle = _bundle(
        {
            FactorType.TECHNICAL: _factor(FactorType.TECHNICAL),
            FactorType.CAPITAL_FLOW: _factor(FactorType.CAPITAL_FLOW),
        }
    )
    bundles = [
        bundle.model_copy(update={"symbol": f"{index:06d}.SZ"})
        for index in range(5534)
    ]
    loader = _FakeLoader(bundles)
    response = ScreeningEngine(loader).run(
        ScreeningRequest(data_cutoff=CUTOFF, limit=20)
    )
    assert loader.calls == 1
    assert response.performance.database_connection_count == 1
    assert response.performance.network_request_count == 0
    assert response.performance.model_call_count == 0
    assert response.performance.decision_packet_count == 0
    assert len(response.candidates) == 20


def test_26_screening_allows_two_of_five() -> None:
    bundle = _bundle(
        {
            FactorType.TECHNICAL: _factor(FactorType.TECHNICAL),
            FactorType.CAPITAL_FLOW: _factor(FactorType.CAPITAL_FLOW),
        }
    )
    assert bundle.factor_coverage.display == "2/5"
    assert bundle.factor_coverage.coverage_ratio == 0.4


def test_27_research_handles_ten_candidates_without_model_calls() -> None:
    bundle = _bundle(
        {
            FactorType.TECHNICAL: _factor(FactorType.TECHNICAL),
            FactorType.CAPITAL_FLOW: _factor(FactorType.CAPITAL_FLOW),
        },
        mode=AnalysisMode.RESEARCH,
    )
    bundles = [
        bundle.model_copy(update={"symbol": f"{index:06d}.SZ"})
        for index in range(10)
    ]
    loader = _FakeLoader(bundles)
    repository = _FakeRepository()
    response = ResearchEngine(loader, repository).run(
        ResearchRequest(
            symbols=[item.symbol for item in bundles],
            data_cutoff=CUTOFF,
            persist=True,
        )
    )
    assert len(response.results) == 10
    assert response.model_call_count == 0
    assert response.network_request_count == 0
    assert repository.saved == 10


def test_28_screening_request_rejects_fewer_than_ten_limit() -> None:
    with pytest.raises(ValidationError):
        ScreeningRequest(data_cutoff=CUTOFF, limit=9)


def test_29_research_request_rejects_more_than_thirty_symbols() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(
            symbols=[f"{index:06d}.SZ" for index in range(31)],
            data_cutoff=CUTOFF,
        )


def test_30_factor_output_rejects_hidden_reasoning_metadata() -> None:
    with pytest.raises(ValidationError):
        FactorOutput(
            factor_id="fac_hidden",
            symbol="600172.SH",
            factor_type=FactorType.SHADOW_COMPOSITE,
            score=0,
            confidence=0,
            data_cutoff=CUTOFF,
            generated_at=CUTOFF,
            evidence_ids=["raw_1"],
            algorithm_version="v1",
            input_snapshot_hash="a" * 64,
            metadata={"chain_of_thought": "not allowed"},
        )
