from __future__ import annotations

import hashlib
from datetime import datetime

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.decision import (
    DecisionShadowEngine,
    formal_result,
)
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    FactorAvailabilityStatus,
    FactorView,
)
from trading.schemas import AnalysisMode


CUTOFF = datetime.fromisoformat("2026-08-07T15:30:00+08:00")


class Loader:
    def __init__(self, bundle) -> None:
        self.bundle = bundle

    def load_many(self, **kwargs):
        return [self.bundle], None


class Repository:
    def save_bundle_and_composite(self, bundle, composite) -> None:
        raise AssertionError("persist_shadow=False must not persist composite")


class FailingRecorder:
    def record(self, request, a_result, formal_calculator) -> None:
        raise RuntimeError("shadow storage unavailable")


def _bundle():
    factor = FactorView(
        factor_output_id="fac_technical",
        factor_type=FactorType.TECHNICAL,
        score=0.8,
        confidence=0.8,
        shadow_mode=True,
        generated_at=CUTOFF,
        data_cutoff=CUTOFF,
        evidence_ids=["hbar_1"],
        risk_flags=[],
        algorithm_version="technical-test-v1",
        input_snapshot_hash=hashlib.sha256(b"technical").hexdigest(),
        availability_status=FactorAvailabilityStatus.AVAILABLE,
        availability_reason="test",
        market_record_ids=["hbar_1"],
        source_factor_output_id="fac_technical",
    )
    return FactorLoader(object()).build_bundle(
        symbol="600000.SH",
        analysis_mode=AnalysisMode.DECISION,
        data_cutoff=CUTOFF,
        factors={FactorType.TECHNICAL: factor},
    )


def test_shadow_failure_cannot_change_or_block_official_a() -> None:
    request = DecisionShadowRequest(
        symbol="600000.SH",
        data_cutoff=CUTOFF,
        technical_score=0.8,
        technical_confidence=0.8,
        fundamental_score=0.2,
        fundamental_confidence=0.7,
        persist_shadow=False,
    )
    expected = formal_result(request).model_dump(mode="json")
    result = DecisionShadowEngine(
        Loader(_bundle()),
        Repository(),
        FailingRecorder(),
    ).run(request)
    assert result.formal_result.model_dump(mode="json") == expected
    assert result.formal_result.shadow_used_for_action is False
    assert result.formal_result.shadow_used_for_veto is False
