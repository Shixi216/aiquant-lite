from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import duckdb
import pytest

from data_hub.schemas.unified import FactorType
from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.user_context import UserContext
from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.canonical_input import (
    CanonicalDecisionInputBuilder,
    CanonicalVetoContext,
)
from trading.decision_support.canonical_shadow import (
    CanonicalDecisionShadowRepository,
    CanonicalDecisionShadowService,
)
from trading.decision_support.decision_engine import DecisionEngine
from trading.decision_support.decision_packet_v2 import (
    DecisionPacketV2Repository,
)
from trading.decision_support.decision_response import (
    DecisionResponse,
    PriceZone,
)
from trading.decision_support.decision_save_service import DecisionSaveService
from trading.decision_support.execution_status import compute_execution_status
from trading.decision_support.manual_position_manager import Holding
from trading.decision_support.task_context import TaskContext
from trading.experiments.formal_strategy_validation import (
    FormalStrategyReplayService,
)
from trading.research.capital_flow.models import MarketBar
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    FactorAvailabilityStatus,
    FactorBundle,
    FactorCoverage,
    FactorView,
    FormalResult,
)
from trading.research.orchestration.service import OrchestrationService
from trading.schemas import Action as LegacyAction
from trading.schemas import AnalysisMode


TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2025, 1, 20, 15, 0, tzinfo=TZ)
SYMBOL = "600000.SH"


@pytest.fixture(autouse=True)
def _no_cross_section_score(monkeypatch):
    """测试隔离：canonical 决策默认不读横截面表，验证回退守卫逻辑。

    四维 composite（cross_section 表）是生产落地项，单测环境不依赖真实
    快照数据；需要验证 composite 覆盖逻辑时单独 monkeypatch resolver。
    """
    monkeypatch.setattr(
        "trading.decision_support.formal_score_resolver.FormalScoreResolver.read_cross_section",
        lambda self, symbol, **kwargs: None,
    )


class _Positions:
    def __init__(self, holding: Holding | None = None) -> None:
        self.holding = holding

    def get_holding(self, local_user_id: str, symbol: str) -> Holding | None:
        return self.holding


class _NoLive:
    @staticmethod
    def can_live_trade(local_user_id: str) -> bool:
        return False


class _NoPlans:
    @staticmethod
    def create_plan(plan: object) -> None:
        raise AssertionError("shadow must not create TradePlan")


class _AuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def save(self, values: dict) -> None:
        self.rows.append(values)


class _AllowSave:
    @staticmethod
    def can_write_local(local_user_id: str) -> bool:
        return True

    @staticmethod
    def parse_natural_language_confirmation(user_input: str) -> bool:
        return True


def _factor(
    kind: FactorType,
    score: float,
    confidence: float,
    status: FactorAvailabilityStatus = FactorAvailabilityStatus.AVAILABLE,
) -> FactorView:
    factor_cutoff = (
        CUTOFF + timedelta(seconds=1)
        if status == FactorAvailabilityStatus.FUTURE_DATA_REJECTED
        else CUTOFF
    )
    return FactorView(
        factor_output_id=f"factor-{kind.value.lower()}",
        factor_type=kind,
        score=score,
        confidence=confidence,
        shadow_mode=True,
        generated_at=CUTOFF,
        data_cutoff=factor_cutoff,
        evidence_ids=[f"evidence-{kind.value.lower()}"],
        risk_flags=[],
        algorithm_version="test-v1",
        input_snapshot_hash="a" * 64,
        availability_status=status,
        availability_reason="deterministic fixture",
        market_record_ids=(
            [f"bar-{index:02d}" for index in range(20)]
            if kind == FactorType.TECHNICAL
            else []
        ),
    )


def _bundle(
    score: float,
    *,
    confidence: float = 0.8,
    capital_score: float = 0.2,
    technical_status: FactorAvailabilityStatus = FactorAvailabilityStatus.AVAILABLE,
    fundamental_missing: bool = False,
) -> FactorBundle:
    technical = _factor(
        FactorType.TECHNICAL,
        score,
        confidence,
        technical_status,
    )
    fundamental = (
        None
        if fundamental_missing
        else _factor(FactorType.FUNDAMENTAL, score, confidence)
    )
    factors = [
        technical,
        fundamental,
        _factor(FactorType.SENTIMENT, 0.9, 0.9),
        _factor(FactorType.POLICY_NEWS, -0.9, 0.9),
        _factor(FactorType.CAPITAL_FLOW, capital_score, 0.8),
    ]
    available = [factor.factor_type for factor in factors if factor is not None]
    missing = [FactorType.FUNDAMENTAL] if fundamental is None else []
    return FactorBundle(
        symbol=SYMBOL,
        analysis_mode=AnalysisMode.DECISION,
        data_cutoff=CUTOFF,
        technical=technical,
        fundamental=fundamental,
        sentiment=factors[2],
        policy_news=factors[3],
        capital_flow=factors[4],
        available_factor_types=available,
        missing_factor_types=missing,
        stale_factor_types=(
            [FactorType.TECHNICAL]
            if technical_status == FactorAvailabilityStatus.STALE
            else []
        ),
        factor_coverage=FactorCoverage(
            available_count=len(available),
            coverage_ratio=len(available) / 5,
            display=f"{len(available)}/5",
        ),
        input_snapshot_hash="b" * 64,
    )


def _bars() -> list[MarketBar]:
    result = []
    for index in range(20):
        close = 98.0 + index * 0.1
        result.append(
            MarketBar(
                canonical_record_id=f"bar-{index:02d}",
                symbol=SYMBOL,
                event_time=CUTOFF - timedelta(days=19 - index),
                data_cutoff=CUTOFF,
                open=close - 0.2,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000_000 + index,
                amount=close * 1_000_000,
            )
        )
    return result


def _legacy(score: float, *, veto: bool = False) -> FormalResult:
    if veto:
        action = LegacyAction.VETO
    elif score > 0.20:
        action = LegacyAction.BUY
    elif score < -0.20:
        action = LegacyAction.SELL
    else:
        action = LegacyAction.HOLD
    return FormalResult(
        strategy_version="formal-60-40-v1",
        score=score,
        confidence=0.8,
        proposal_action=action,
        final_action=action,
        hard_veto=veto,
        formal_weights={"technical": 0.60, "fundamental": 0.40},
    )


def _request(score: float, *, veto: bool = False) -> DecisionShadowRequest:
    return DecisionShadowRequest(
        symbol=SYMBOL,
        data_cutoff=CUTOFF,
        technical_score=score,
        technical_confidence=0.8,
        fundamental_score=score,
        fundamental_confidence=0.8,
        hard_veto=veto,
        persist_shadow=False,
    )


def _user() -> UserContext:
    return UserContext.authenticated_context(
        local_user_id="user-1",
        external_user_id="external-1",
        channel="test",
        source="TEST_AUTH",
    )


def _portfolio(*holdings: Holding) -> PortfolioContext:
    return PortfolioContext.research_default(
        local_user_id="user-1",
        as_of=CUTOFF,
        holdings=tuple(holdings),
    )


def _engine(positions: _Positions) -> DecisionEngine:
    return DecisionEngine(
        permission_service=_NoLive(),
        manual_positions=positions,
        plan_repo=_NoPlans(),
    )


@pytest.mark.parametrize(
    ("name", "score", "confidence", "holding", "capital", "status", "missing", "veto"),
    [
        ("high_no_position", 0.80, 0.80, None, 0.2, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("medium_low_confidence", 0.50, 0.40, None, 0.2, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("veto", 0.80, 0.80, None, 0.2, FactorAvailabilityStatus.AVAILABLE, False, True),
        ("stale", 0.80, 0.80, None, 0.2, FactorAvailabilityStatus.STALE, False, False),
        ("pit_failed", 0.80, 0.80, None, 0.2, FactorAvailabilityStatus.FUTURE_DATA_REJECTED, False, False),
        ("add", 0.80, 0.80, "holding", 0.5, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("hold", 0.80, 0.80, "holding", -0.5, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("reduce", -0.30, 0.80, "holding", 0.2, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("exit", -0.80, 0.80, "holding", 0.2, FactorAvailabilityStatus.AVAILABLE, False, False),
        ("fundamental_missing", 0.80, 0.80, None, 0.2, FactorAvailabilityStatus.AVAILABLE, True, False),
    ],
)
def test_realtime_canonical_shadow_equals_direct_decision_engine(
    name: str,
    score: float,
    confidence: float,
    holding: str | None,
    capital: float,
    status: FactorAvailabilityStatus,
    missing: bool,
    veto: bool,
) -> None:
    del name
    current_holding = (
        Holding(
            local_user_id="user-1",
            symbol=SYMBOL,
            quantity=200,
            average_cost=100,
        )
        if holding
        else None
    )
    positions = _Positions(current_holding)
    bundle = _bundle(
        score,
        confidence=confidence,
        capital_score=capital,
        technical_status=status,
        fundamental_missing=missing,
    )
    trusted_score = score if not missing else score * 0.60
    legacy = _legacy(trusted_score, veto=veto)
    veto_context = CanonicalVetoContext(
        result=(
            VetoResult(
                veto_triggered=True,
                veto_type="TEST",
                veto_reason="fixed veto",
                evidence_source="historical formal risk",
            )
            if veto
            else VetoResult(veto_triggered=False)
        ),
        source="HISTORICAL_CANDIDATE_VETO",
        reason="fixed complete PIT risk input",
        input_complete=True,
    )
    builder = CanonicalDecisionInputBuilder(positions)
    built = builder.build(
        request_id="request-1",
        user_context=_user(),
        request=_request(score, veto=veto),
        bundle=bundle,
        market_bars=_bars(),
        legacy_result=legacy,
        portfolio_context=_portfolio(),
        veto_context=veto_context,
    )
    assert built.decision_input.task.local_user_id == "user-1"
    assert built.decision_input.task.external_user_id == "external-1"
    assert built.decision_input.task.channel == "test"
    engine = _engine(positions)
    direct = engine.decide(built.decision_input)
    repo = _AuditRepo()
    shadow = CanonicalDecisionShadowService(
        position_reader=positions,
        engine=engine,
        repository=repo,
    ).run_and_record(
        request_id="request-1",
        user_context=_user(),
        request=_request(score, veto=veto),
        bundle=bundle,
        market_bars=_bars(),
        legacy_result=legacy,
        veto_context=veto_context,
        veto_event_count=0,
        legacy_latency_ms=0.0,
    )

    assert shadow.response.to_dict() == direct.to_dict()
    # G 守卫（P0）：无四维 composite（cross_section 读不到）时回退 legacy 60/40，
    # 强制 formal_score=0.0（WAIT 区间），不再用 tech*0.6+fund*0.4 出分。
    assert repo.rows[0]["canonical_score"] == pytest.approx(0.0)
    assert repo.rows[0]["user_context_source"] == "TEST_AUTH"
    assert repo.rows[0]["account_context_source"] == (
        "RESEARCH_DEFAULT_ACCOUNT_CONTEXT"
    )
    assert "ACCOUNT_CONTEXT_ASSUMPTION" in (
        repo.rows[0]["divergence_categories_json"]
    )
    assert repo.rows[0]["action_diverged"] is True


def test_score_source_guard_uses_factor_bundle_and_records_mismatch() -> None:
    positions = _Positions()
    request = _request(-0.9)
    bundle = _bundle(0.8)
    legacy = _legacy(-0.9)
    built = CanonicalDecisionInputBuilder(positions).build(
        request_id="mismatch",
        user_context=_user(),
        request=request,
        bundle=bundle,
        market_bars=_bars(),
        legacy_result=legacy,
        portfolio_context=_portfolio(),
        veto_context=CanonicalVetoContext(
            result=None,
            source="HISTORICAL_CANDIDATE_VETO",
            reason="VETO_INPUT_INCOMPLETE",
            input_complete=False,
        ),
    )

    # G 守卫：无四维 composite → 回退 legacy 60/40，formal_score 强制 0.0（WAIT）
    assert built.decision_input.formal_score == pytest.approx(0.0)
    assert built.score_source == "legacy_60_40_fallback"
    assert built.score_source_mismatch is True
    assert built.score_source_reason == (
        "legacy_60_40_fallback:SCORE_SOURCE_MISMATCH:"
        "technical_score,fundamental_score,formal_score"
    )
    assert built.veto_input_complete is False


def test_cross_section_composite_overrides_60_40(monkeypatch) -> None:
    """四维 composite 覆盖 60/40（横截面表有数据时）。

    生产落地后，formal_score 优先取 cross_section 表的 composite
    （技术30/基本30/情绪25/资金15 的 rank 加权），读不到才走 G 守卫回退。
    """
    monkeypatch.setattr(
        "trading.decision_support.formal_score_resolver.FormalScoreResolver.read_cross_section",
        lambda self, symbol, **kwargs: (0.75, None),
    )
    positions = _Positions()
    request = _request(-0.9)
    bundle = _bundle(0.8)
    legacy = _legacy(-0.9)
    built = CanonicalDecisionInputBuilder(positions).build(
        request_id="composite",
        user_context=_user(),
        request=request,
        bundle=bundle,
        market_bars=_bars(),
        legacy_result=legacy,
        portfolio_context=_portfolio(),
        veto_context=CanonicalVetoContext(
            result=None,
            source="HISTORICAL_CANDIDATE_VETO",
            reason="VETO_INPUT_INCOMPLETE",
            input_complete=False,
        ),
    )
    # 60/40 本应是 0.8*0.6 + 0.8*0.4 = 0.8，被 composite 覆盖为 0.75
    assert built.decision_input.formal_score == pytest.approx(0.75)


@pytest.mark.parametrize("audit_raises", [False, True])
def test_orchestration_returns_identical_legacy_object_when_shadow_runs(
    monkeypatch,
    audit_raises: bool,
) -> None:
    legacy = SimpleNamespace(formal_result=_legacy(0.8))
    bundle = _bundle(0.8)

    class _LegacyEngine:
        @staticmethod
        def run_with_bundle(request):
            return legacy, bundle

    class _ResearchRepository:
        @staticmethod
        def load_bulk(**kwargs):
            return SimpleNamespace(bars_by_symbol={SYMBOL: _bars()})

    class _CanonicalAudit:
        def run_and_record(self, **kwargs):
            if audit_raises:
                raise RuntimeError("audit-only failure")
            return SimpleNamespace(response=object())

    monkeypatch.setattr(
        "trading.decision_support.canonical_shadow."
        "CanonicalDecisionShadowService",
        _CanonicalAudit,
    )
    service = object.__new__(OrchestrationService)
    service.decision_engine = _LegacyEngine()
    service.repository = _ResearchRepository()

    result = service.decision_shadow_with_canonical_audit(
        _request(0.8),
        request_id="legacy-identity",
        user_context=_user(),
    )

    assert result is legacy


def test_pit_false_changes_buildable_position_semantics() -> None:
    positions = _Positions()
    bundle = _bundle(0.8)
    built = CanonicalDecisionInputBuilder(positions).build(
        request_id="pit",
        user_context=_user(),
        request=_request(0.8),
        bundle=bundle,
        market_bars=_bars(),
        legacy_result=_legacy(0.8),
        portfolio_context=_portfolio(),
        veto_context=CanonicalVetoContext(
            result=VetoResult(veto_triggered=False),
            source="HISTORICAL_CANDIDATE_VETO",
            reason="complete",
            input_complete=True,
        ),
    )
    engine = _engine(positions)
    pit_true = engine.decide(built.decision_input)
    pit_false = engine.decide(
        replace(built.decision_input, point_in_time_ok=False)
    )

    # G 守卫：回退 legacy 60/40 → formal_score 强制 0.0 → 无论 PIT，动作均为 WAIT
    assert pit_true.allow_new_position is False
    assert pit_false.allow_new_position is False
    assert pit_true.action.value == "WAIT"
    assert pit_false.action.value == "WAIT"


def test_historical_engine_and_realtime_canonical_use_same_fixed_input() -> None:
    positions = _Positions()
    built = CanonicalDecisionInputBuilder(positions).build(
        request_id="history",
        user_context=_user(),
        request=_request(0.8),
        bundle=_bundle(0.8),
        market_bars=_bars(),
        legacy_result=_legacy(0.8),
        portfolio_context=_portfolio(),
        veto_context=CanonicalVetoContext(
            result=VetoResult(veto_triggered=False),
            source="HISTORICAL_CANDIDATE_VETO",
            reason="complete",
            input_complete=True,
        ),
    )
    canonical_engine = _engine(positions)
    historical_engine = FormalStrategyReplayService(
        engine=_engine(positions)
    ).engine

    assert canonical_engine.decide(built.decision_input).to_dict() == (
        historical_engine.decide(built.decision_input).to_dict()
    )


@pytest.mark.parametrize("action", ["REDUCE", "EXIT", "STOP_LOSS"])
def test_none_stop_loss_is_absent_for_reduction_actions(action: str) -> None:
    status, _ = compute_execution_status(
        current_price=100,
        action=action,
        veto_triggered=False,
        frozen_entry_zone=[],
        frozen_preferred_zone=[],
        frozen_stop_loss_price=None,
    )
    assert status


def test_none_stop_loss_is_absent_for_buy_below_preferred_zone() -> None:
    status, _ = compute_execution_status(
        current_price=94,
        action="BUY",
        veto_triggered=False,
        frozen_entry_zone=[95, 101],
        frozen_preferred_zone=[95, 97.4],
        frozen_stop_loss_price=None,
    )
    assert status


def test_execution_status_uses_existing_frozen_zones() -> None:
    preferred, _ = compute_execution_status(
        current_price=96,
        action="BUY",
        veto_triggered=False,
        frozen_entry_zone=[95, 101],
        frozen_preferred_zone=[95, 97.4],
        frozen_stop_loss_price=90,
    )
    chase, _ = compute_execution_status(
        current_price=102,
        action="BUY",
        veto_triggered=False,
        frozen_entry_zone=[95, 101],
        frozen_preferred_zone=[95, 97.4],
        frozen_stop_loss_price=90,
    )
    stopped, _ = compute_execution_status(
        current_price=89,
        action="BUY",
        veto_triggered=False,
        frozen_entry_zone=[95, 101],
        frozen_preferred_zone=[95, 97.4],
        frozen_stop_loss_price=90,
    )
    assert len({preferred, chase, stopped}) == 3


def test_decision_packet_round_trip_preserves_frozen_zones_and_hash(
    tmp_path,
) -> None:
    response = DecisionResponse(
        task_context=TaskContext(
            local_user_id="user-1",
            external_user_id="user-1",
            symbol=SYMBOL,
            snapshot_id="snapshot-1",
            data_cutoff=CUTOFF,
        ),
        formal_score=0.8,
        confidence=0.8,
        price_zones=PriceZone(
            entry_zone=[95.0, 101.0],
            preferred_zone=[95.0, 97.4],
            stop_loss_price=None,
        ),
        frozen_entry_zone=[95.0, 101.0],
        frozen_preferred_zone=[95.0, 97.4],
        frozen_stop_loss_price=None,
        zone_version="V1-fixed",
    )
    repo = DecisionPacketV2Repository(tmp_path / "packet.duckdb")
    result = DecisionSaveService(
        packet_repo=repo,
        permission_service=_AllowSave(),
    ).save_decision(response, confirmed=True)
    saved = repo.get(result["decision_id"])

    assert saved is not None
    assert saved.frozen_entry_zone == response.frozen_entry_zone
    assert saved.frozen_preferred_zone == response.frozen_preferred_zone
    assert saved.frozen_stop_loss_price is None
    assert saved.zone_version == response.zone_version
    assert saved.compute_sha256() == saved.content_sha256
    changed = replace(
        saved,
        frozen_entry_zone=[94.0, 101.0],
        content_sha256="",
    )
    assert changed.compute_sha256() != saved.content_sha256



def test_canonical_shadow_audit_migration_and_repository_insert(
    monkeypatch,
) -> None:
    from contextlib import contextmanager

    from database.migrations.v0119_canonical_shadow_observation_v2 import (
        apply_migration,
    )
    from database.migrations.v0120_canonical_shadow_real_observation import (
        apply_migration as apply_real_observation_migration,
    )

    connection = duckdb.connect(":memory:")
    assert apply_migration(connection) is True
    assert apply_migration(connection) is False
    assert apply_real_observation_migration(connection) is True
    assert apply_real_observation_migration(connection) is False

    @contextmanager
    def _connection(*, read_only=False):
        del read_only
        yield connection

    monkeypatch.setattr(
        "trading.decision_support.canonical_shadow.get_connection",
        _connection,
    )
    repository = CanonicalDecisionShadowRepository()
    repository.save_error_observation(
        request_id="request-1",
        user_context=_user(),
        request=_request(0.8),
        legacy_result=_legacy(0.8),
        legacy_latency_ms=1.0,
        canonical_latency_ms=2.0,
        canonical_error="RuntimeError",
    )
    row = connection.execute(
        """
        SELECT research_only, affects_legacy_output, creates_order,
               creates_trade, creates_trade_plan, creates_decision_packet,
               canonical_success
        FROM canonical_shadow_observations_v2
        """
    ).fetchone()
    assert row == (True, False, False, False, False, False, False)
    connection.close()
