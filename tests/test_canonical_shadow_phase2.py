from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from database.migrations.v0119_canonical_shadow_observation_v2 import (
    apply_migration,
)
from database.migrations.v0120_canonical_shadow_real_observation import (
    apply_migration as apply_real_observation_migration,
)
from trading.decision_support.canonical_shadow import (
    CanonicalDecisionShadowRepository,
    classify_divergence,
    formal_score_bucket,
)
from trading.decision_support.formal_veto import (
    FORMAL_VETO_SOURCE,
    FormalRiskEvent,
    FormalRiskInput,
    evaluate_formal_veto,
)
from trading.decision_support.manual_position_manager import Holding
from trading.decision_support.portfolio_context import (
    HISTORICAL_SIMULATED_ACCOUNT_SOURCE,
    MANUAL_USER_ACCOUNT_SOURCE,
    RESEARCH_DEFAULT_ACCOUNT_SOURCE,
    PortfolioContext,
)
from trading.decision_support.user_context import UserContext
from trading.decision_support.orchestrator.service import (
    TradingDecisionService,
    _generate_strategy,
)
from trading.research.orchestration.repository import OrchestrationRepository
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    FormalResult,
)
from trading.research.orchestration.service import OrchestrationService
from trading.schemas import Action as LegacyAction


TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2025, 1, 20, 15, 0, tzinfo=TZ)
SYMBOL = "600000.SH"


def _user() -> UserContext:
    return UserContext.authenticated_context(
        local_user_id="user-1",
        external_user_id="external-1",
        channel="internal-test",
        source="TEST_AUTHENTICATED_CONTEXT",
    )


def _request() -> DecisionShadowRequest:
    return DecisionShadowRequest(
        symbol=SYMBOL,
        data_cutoff=CUTOFF,
        technical_score=0.8,
        technical_confidence=0.8,
        fundamental_score=0.8,
        fundamental_confidence=0.8,
        persist_shadow=False,
    )


def _legacy() -> FormalResult:
    return FormalResult(
        strategy_version="formal-60-40-v1",
        score=0.8,
        confidence=0.8,
        proposal_action=LegacyAction.BUY,
        final_action=LegacyAction.BUY,
        hard_veto=False,
        formal_weights={"technical": 0.6, "fundamental": 0.4},
    )


def _risk_event(
    *,
    title: str = "普通经营公告",
    event_time: datetime = CUTOFF - timedelta(hours=2),
    available_time: datetime = CUTOFF - timedelta(hours=1),
) -> FormalRiskEvent:
    return FormalRiskEvent(
        event_id="event-1",
        title=title,
        risk_category="announcement",
        event_time=event_time,
        data_available_time=available_time,
        data_cutoff=available_time,
    )


def _risk_input(
    *,
    title: str = "普通经营公告",
    event_complete: bool = True,
    status_complete: bool = True,
) -> FormalRiskInput:
    return FormalRiskInput(
        events=(_risk_event(title=title),),
        suspended=False,
        delisted=False,
        st_status=False,
        event_coverage_complete=event_complete,
        security_status_complete=status_complete,
        requested_cutoff=CUTOFF,
    )


def test_complete_realtime_veto_uses_historical_candidate_service() -> None:
    evaluation = evaluate_formal_veto(
        _risk_input(title="公司收到立案调查通知")
    )

    assert evaluation.source == FORMAL_VETO_SOURCE
    assert evaluation.input_complete is True
    assert evaluation.result is not None
    assert evaluation.result.veto_triggered is True
    assert evaluation.event_count == 1


def test_complete_non_risk_input_returns_explicit_false_veto() -> None:
    evaluation = evaluate_formal_veto(_risk_input())

    assert evaluation.input_complete is True
    assert evaluation.result is not None
    assert evaluation.result.veto_triggered is False
    assert evaluation.reason == "VETO_INPUT_COMPLETE:NO_FORMAL_RISK_MATCH"


def test_incomplete_non_risk_input_never_defaults_to_false() -> None:
    evaluation = evaluate_formal_veto(
        _risk_input(event_complete=False, status_complete=False)
    )

    assert evaluation.input_complete is False
    assert evaluation.result is None
    assert evaluation.reason == (
        "VETO_INPUT_INCOMPLETE:EVENT_COVERAGE,SECURITY_STATUS"
    )


def test_incomplete_input_can_preserve_a_positive_known_veto() -> None:
    evaluation = evaluate_formal_veto(
        _risk_input(
            title="公司收到立案调查通知",
            event_complete=False,
        )
    )

    assert evaluation.input_complete is False
    assert evaluation.result is not None
    assert evaluation.result.veto_triggered is True


def test_future_risk_event_is_rejected_and_marks_input_incomplete() -> None:
    value = FormalRiskInput(
        events=(
            _risk_event(
                event_time=CUTOFF + timedelta(seconds=1),
                available_time=CUTOFF + timedelta(seconds=1),
            ),
        ),
        suspended=False,
        delisted=False,
        st_status=False,
        event_coverage_complete=True,
        security_status_complete=True,
        requested_cutoff=CUTOFF,
    )
    evaluation = evaluate_formal_veto(value)

    assert evaluation.input_complete is False
    assert evaluation.result is None
    assert evaluation.event_count == 0
    assert "PIT_TIME" in evaluation.reason


def _risk_database() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE stock_universe (
            symbol VARCHAR, listing_status VARCHAR, delist_date DATE,
            is_st BOOLEAN, is_suspended BOOLEAN,
            verification_status VARCHAR, data_available_time TIMESTAMPTZ,
            updated_at TIMESTAMPTZ, universe_version VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE event_clusters (
            event_cluster_id VARCHAR, canonical_title VARCHAR,
            event_type VARCHAR, event_time TIMESTAMPTZ,
            data_cutoff TIMESTAMPTZ, primary_source_id VARCHAR
        )
        """
    )
    connection.execute(
        "CREATE TABLE event_symbol_links (event_cluster_id VARCHAR, symbol VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE data_records (record_id VARCHAR, fetched_at TIMESTAMPTZ)"
    )
    connection.execute(
        """
        CREATE TABLE sentiment_symbol_snapshots (
            snapshot_id VARCHAR, symbol VARCHAR, analysis_mode VARCHAR,
            data_cutoff TIMESTAMPTZ, generated_at TIMESTAMPTZ,
            missing_fields_json JSON
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE policy_news_symbol_snapshots (
            snapshot_id VARCHAR, symbol VARCHAR, analysis_mode VARCHAR,
            data_cutoff TIMESTAMPTZ, generated_at TIMESTAMPTZ,
            missing_fields_json JSON
        )
        """
    )
    connection.execute(
        "INSERT INTO stock_universe VALUES (?, 'ACTIVE', NULL, FALSE, FALSE, "
        "'VERIFIED', ?, ?, 'v1')",
        [SYMBOL, CUTOFF - timedelta(hours=2), CUTOFF - timedelta(hours=2)],
    )
    connection.execute(
        "INSERT INTO data_records VALUES ('source-1', ?), ('future-source', ?)",
        [CUTOFF - timedelta(hours=1), CUTOFF + timedelta(hours=1)],
    )
    connection.execute(
        "INSERT INTO event_clusters VALUES "
        "('event-1', '公司收到立案调查通知', 'announcement', ?, ?, 'source-1'), "
        "('future-event', '退市风险', 'announcement', ?, ?, 'future-source')",
        [
            CUTOFF - timedelta(hours=2),
            CUTOFF - timedelta(hours=1),
            CUTOFF + timedelta(hours=1),
            CUTOFF + timedelta(hours=1),
        ],
    )
    connection.execute(
        "INSERT INTO event_symbol_links VALUES ('event-1', ?), "
        "('future-event', ?)",
        [SYMBOL, SYMBOL],
    )
    for table in (
        "sentiment_symbol_snapshots",
        "policy_news_symbol_snapshots",
    ):
        connection.execute(
            f"INSERT INTO {table} VALUES (?, ?, 'DECISION', ?, ?, '[]')",
            [f"{table}-1", SYMBOL, CUTOFF, CUTOFF],
        )
    return connection


def test_repository_builds_complete_pit_veto_input(monkeypatch) -> None:
    connection = _risk_database()

    @contextmanager
    def _connection():
        yield connection

    monkeypatch.setattr(
        "trading.research.orchestration.repository.get_connection",
        _connection,
    )
    repository = object.__new__(OrchestrationRepository)
    value = repository.load_formal_risk_input(
        symbol=SYMBOL,
        data_cutoff=CUTOFF,
    )
    evaluation = evaluate_formal_veto(value)

    assert [event.event_id for event in value.events] == ["event-1"]
    assert value.events[0].risk_category == "announcement"
    assert value.event_coverage_complete is True
    assert value.security_status_complete is True
    assert evaluation.input_complete is True
    assert evaluation.result is not None
    assert evaluation.result.veto_triggered is True
    connection.close()


def test_finance_mcp_context_is_explicitly_unauthenticated_and_isolated() -> None:
    context = UserContext.finance_mcp_shadow()

    assert context.local_user_id == "finance-mcp-shadow"
    assert context.external_user_id == "finance-mcp-shadow"
    assert context.channel == "finance-mcp"
    assert context.authenticated is False
    assert context.source == "UNAUTHENTICATED_FINANCE_MCP_ISOLATION"


def test_authenticated_user_context_preserves_all_identity_fields() -> None:
    context = _user()

    assert context.local_user_id == "user-1"
    assert context.external_user_id == "external-1"
    assert context.channel == "internal-test"
    assert context.authenticated is True


def test_portfolio_context_sources_are_explicit() -> None:
    holding = Holding(local_user_id="user-1", symbol=SYMBOL, quantity=100)
    research = PortfolioContext.research_default(
        local_user_id="user-1", as_of=CUTOFF, holdings=(holding,)
    )
    historical = PortfolioContext.historical_simulated(
        local_user_id="user-1", as_of=CUTOFF
    )
    manual = PortfolioContext.manual_user(
        local_user_id="user-1",
        total_assets=250_000,
        available_cash=80_000,
        holdings=(holding,),
        as_of=CUTOFF,
    )

    assert research.source == RESEARCH_DEFAULT_ACCOUNT_SOURCE
    assert research.is_research_assumption is True
    assert historical.source == HISTORICAL_SIMULATED_ACCOUNT_SOURCE
    assert historical.is_research_assumption is True
    assert manual.source == MANUAL_USER_ACCOUNT_SOURCE
    assert manual.is_research_assumption is False
    assert manual.holding_for(SYMBOL) is holding


def test_manual_portfolio_requires_real_total_assets() -> None:
    with pytest.raises(ValueError, match="total_assets"):
        PortfolioContext.manual_user(
            local_user_id="user-1",
            total_assets=0,
            available_cash=0,
            holdings=(),
            as_of=CUTOFF,
        )


def test_portfolio_rejects_cross_user_holdings() -> None:
    holding = Holding(local_user_id="other-user", symbol=SYMBOL, quantity=100)
    with pytest.raises(ValueError, match="identity"):
        PortfolioContext.research_default(
            local_user_id="user-1", as_of=CUTOFF, holdings=(holding,)
        )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (-0.6, "LT_-0.5"),
        (-0.3, "-0.5_TO_-0.2"),
        (0.0, "-0.2_TO_0.2"),
        (0.3, "0.2_TO_0.4"),
        (0.5, "0.4_TO_0.65"),
        (0.8, "GE_0.65"),
    ],
)
def test_formal_score_buckets(score: float, expected: str) -> None:
    assert formal_score_bucket(score) == expected


def test_divergence_classifier_records_specific_causes() -> None:
    categories = classify_divergence(
        legacy_score=0.8,
        canonical_score=0.8,
        legacy_action="BUY",
        canonical_action="STRONG_BUY",
        legacy_veto=False,
        canonical_veto=True,
        score_source_mismatch=True,
        canonical_pit=False,
        canonical_data_status="STALE",
        veto_input_complete=False,
        canonical_current_position=0.1,
        account_context_assumption=True,
    )

    assert set(categories) == {
        "EXPECTED_THRESHOLD_DIFFERENCE",
        "SCORE_SOURCE_MISMATCH",
        "PIT_DIFFERENCE",
        "DATA_STATUS_DIFFERENCE",
        "VETO_DIFFERENCE",
        "VETO_INPUT_INCOMPLETE",
        "POSITION_CONTEXT_DIFFERENCE",
        "ACCOUNT_CONTEXT_ASSUMPTION",
        "LEGACY_MISSING_SEMANTICS",
    }


def test_shadow_exception_records_error_and_returns_same_legacy(
    monkeypatch,
) -> None:
    legacy = SimpleNamespace(formal_result=_legacy())
    bundle = SimpleNamespace(
        technical=SimpleNamespace(market_record_ids=[]),
    )
    captured: list[dict] = []

    class _LegacyEngine:
        @staticmethod
        def run_with_bundle(request):
            return legacy, bundle

    class _ResearchRepository:
        @staticmethod
        def load_formal_risk_input(**kwargs):
            return _risk_input()

        @staticmethod
        def load_bulk(**kwargs):
            return SimpleNamespace(bars_by_symbol={SYMBOL: []})

    class _ObservationRepository:
        def save_error_observation(self, **kwargs):
            captured.append(kwargs)

    class _CanonicalService:
        def __init__(self, **kwargs):
            pass

        def run_and_record(self, **kwargs):
            raise RuntimeError("shadow failure")

    monkeypatch.setattr(
        "trading.decision_support.canonical_shadow."
        "CanonicalDecisionShadowRepository",
        _ObservationRepository,
    )
    monkeypatch.setattr(
        "trading.decision_support.canonical_shadow."
        "CanonicalDecisionShadowService",
        _CanonicalService,
    )
    service = object.__new__(OrchestrationService)
    service.decision_engine = _LegacyEngine()
    service.repository = _ResearchRepository()

    result = service.decision_shadow_with_canonical_audit(
        _request(),
        request_id="request-1",
        user_context=_user(),
    )

    assert result is legacy
    assert captured[0]["canonical_error"] == "RuntimeError"
    assert captured[0]["legacy_result"] is legacy.formal_result


def test_shadow_stats_report_counts_and_latency(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    apply_migration(connection)
    apply_real_observation_migration(connection)

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
        request=_request(),
        legacy_result=_legacy(),
        legacy_latency_ms=3.0,
        canonical_latency_ms=7.0,
        canonical_error="RuntimeError",
    )
    stats = repository.stats(record_source="TEST")

    assert stats["total"] == 1
    assert stats["canonical_success"] == 0
    assert stats["canonical_error_rate"] == pytest.approx(1.0)
    assert stats["legacy_success_rate"] == pytest.approx(1.0)
    assert stats["veto_input_incomplete"] == 1
    assert stats["veto_input_complete_rate"] == pytest.approx(0.0)
    assert stats["average_legacy_latency_ms"] == pytest.approx(3.0)
    assert stats["average_canonical_latency_ms"] == pytest.approx(7.0)
    assert stats["average_shadow_overhead_ms"] == pytest.approx(7.0)
    assert stats["divergence_categories"]["CANONICAL_ERROR"] == 1
    connection.close()


def test_third_decision_entry_is_public_distinct_and_not_deleted() -> None:
    routes_source = (
        Path(__file__).resolve().parents[1] / "trading" / "routes.py"
    ).read_text(encoding="utf-8")
    assert 'decision_router = APIRouter(prefix="/v1/decisions"' in routes_source
    assert '@decision_router.post("/from-data"' in routes_source
    assert 'legacy_router = APIRouter(prefix="/v1/trading"' in routes_source
    assert '@legacy_router.post(' in routes_source
    assert '"/decisions/from-data"' in routes_source
    assert "deprecated=True" in routes_source

    strategy_source = inspect.getsource(_generate_strategy)
    service_source = inspect.getsource(TradingDecisionService.decide)
    assert "combined >= 0.2" in strategy_source
    assert "evaluate_strategy_risk" in service_source
    assert "final_target_weight" in service_source
    assert "DecisionEngine" not in service_source
