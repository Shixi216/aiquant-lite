"""The same PIT snapshot, rather than request/proxy scores, is the formal baseline."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import duckdb
import pytest

from config.settings import settings
from trading.decision_support.formal_score_resolver import FormalScoreResolver
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.schemas import DecisionShadowRequest


CUTOFF = datetime(2026, 8, 10, 18, tzinfo=timezone.utc)
EXPECTED = .8 * .30 + .2 * .30 - .4 * .25 + .6 * .15


@pytest.fixture
def snapshot_db(tmp_path, monkeypatch):
    path = tmp_path / "scores.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    connection = duckdb.connect(str(path))
    ddl = """(snapshot_id VARCHAR, symbol VARCHAR, data_cutoff TIMESTAMPTZ,
        tech_rank DOUBLE, fund_rank DOUBLE, sent_rank DOUBLE, margin_rank DOUBLE,
        composite_score DOUBLE, generated_at TIMESTAMPTZ)"""
    for table in ("cross_section_factor_snapshot", "cross_section_factor_snapshot_history"):
        connection.execute(f"CREATE TABLE {table} {ddl}")
    past = ("past", "600000.SH", CUTOFF, .8, .2, -.4, .6, EXPECTED, CUTOFF)
    future = ("future", "600000.SH", CUTOFF + timedelta(days=1), -1, -1, -1, -1,
              -1, CUTOFF + timedelta(days=1))
    connection.executemany("INSERT INTO cross_section_factor_snapshot_history VALUES (?,?,?,?,?,?,?,?,?)", [past, future])
    connection.execute("INSERT INTO cross_section_factor_snapshot VALUES (?,?,?,?,?,?,?,?,?)", future)
    connection.close()
    return path


def request(**overrides):
    fields = dict(symbol="600000.SH", data_cutoff=CUTOFF, technical_score=1,
                  fundamental_score=1, technical_confidence=.9, fundamental_confidence=.8,
                  persist_shadow=False)
    return DecisionShadowRequest(**(fields | overrides))


@pytest.mark.parametrize("symbol", ["600000", "600000.SH"])
def test_formal_uses_same_pit_snapshot_despite_request_scores_or_weights(snapshot_db, symbol):
    result = formal_result(request(symbol=symbol, sentiment_score=-1,
                                   custom_formal_weights={"technical": 1}))
    resolved = FormalScoreResolver().resolve(symbol, -1, -1, data_cutoff=CUTOFF, with_detail=True)
    assert result.score == resolved.score == pytest.approx(EXPECTED)
    assert result.status == "AVAILABLE"
    assert result.score_source == resolved.source == "cross_section_4d"
    assert result.formal_weights == {"TECHNICAL": .30, "FUNDAMENTAL": .30, "SENTIMENT": .25, "MARGIN": .15}
    assert resolved.detail["sent_rank"] == -.4
    assert formal_result(request(symbol=symbol, hard_veto=True)).final_action.value == "veto"


def test_earlier_cutoff_cannot_use_latest_or_cached_future_snapshot(snapshot_db):
    resolver = FormalScoreResolver()
    assert resolver.resolve("600000", 1, 1, data_cutoff=CUTOFF + timedelta(days=1)).score == -1
    before = CUTOFF - timedelta(seconds=1)
    result = resolver.resolve("600000", 1, 1, data_cutoff=before)
    assert result.degraded
    assert result.source == "cross_section_4d_unavailable"
    assert result.score == 0
    formal = formal_result(request(data_cutoff=before))
    assert formal.status == "UNAVAILABLE"
    assert formal.proposal_action.value == "hold"


@pytest.mark.parametrize("score", [None, float("nan"), float("inf"), 1.01, -1.01])
def test_missing_or_invalid_snapshot_cannot_fall_back_to_high_request_scores(snapshot_db, score):
    with duckdb.connect(str(snapshot_db)) as connection:
        connection.execute("UPDATE cross_section_factor_snapshot_history SET composite_score=? WHERE snapshot_id='past'", [score])
    result = formal_result(request())
    assert result.status == "UNAVAILABLE"
    assert result.score == result.confidence == 0
    assert result.final_action.value == "hold"


def _stock_report(monkeypatch, cutoff):
    import trading.research.stock_report_service as module
    from trading.schemas import Bar

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz)

    captured = []

    class Engine:
        def __init__(self, **kwargs):
            pass

        def decide(self, value):
            captured.append(value)
            return SimpleNamespace(action_zh="等待", execution_status="等待确认", next_action="等待",
                                   current_position_ratio=0., target_position_ratio=0., recommended_batches=0,
                                   frozen_entry_zone=None, frozen_preferred_zone=None, frozen_stop_loss_price=None)

    bar = Bar(trade_date=date(2026, 8, 10), open=10, high=11, low=9, close=10, volume=100)
    monkeypatch.setattr(module, "datetime", FixedTime)
    monkeypatch.setattr(module.StockReportService, "_load_inputs", lambda *args: ([bar], [], [], [], bar.trade_date))
    monkeypatch.setattr(module, "NameChangeService", lambda: SimpleNamespace(get_history=lambda *a, **k: None))
    monkeypatch.setattr(module, "render_name_change_history", lambda *a, **k: "")
    monkeypatch.setattr(module, "technical_factor", lambda *a: {"score": 1., "confidence": .9, "summary": "tech"})
    monkeypatch.setattr(module, "fundamental_factor", lambda *a: {"score": 1., "confidence": .8, "summary": "fund"})
    monkeypatch.setattr(module, "capital_flow_factor_enhanced", lambda *a, **k: {"score": 0., "pv_label": "", "label": ""})
    monkeypatch.setattr(module, "sentiment_factor", lambda *a: {"score": 0., "events": []})
    monkeypatch.setattr(module, "policy_factor", lambda *a: {"score": 0., "notes": [], "total_announcements": 0})
    monkeypatch.setattr(module, "DecisionEngine", Engine)
    report = module.StockReportService().run(symbol="600000", no_fetch=True, no_ai=True)
    assert len(captured) == 1, report
    return report, captured[0]


def test_stock_report_and_mcp_formal_result_have_identical_cutoff_score(snapshot_db, monkeypatch):
    report, decision_input = _stock_report(monkeypatch, CUTOFF)
    formal = formal_result(request())
    assert decision_input.formal_score == formal.score == pytest.approx(EXPECTED)
    assert "四维综合分 +0.2900" in report
    assert decision_input.data_status.value == "FRESH"


def test_stock_report_missing_snapshot_displays_na_and_blocks_formal_decision(snapshot_db, monkeypatch):
    report, decision_input = _stock_report(monkeypatch, CUTOFF - timedelta(days=1))
    assert decision_input.data_status.value == "FAILED"
    assert decision_input.confidence == 0
    assert "四维综合分 N/A" in report
    assert "60/40" not in report


def test_mcp_workflow_does_not_mask_unavailable_score_as_available(snapshot_db):
    from router.integration.schemas import DecisionWorkflowRequest
    from router.integration.workflow import InteractionWorkflowService

    def run(req):
        return SimpleNamespace(
            formal_result=formal_result(req), missing_factors=[], available_factors=[],
            shadow_composite=SimpleNamespace(model_dump=lambda **kw: {}),
            factor_coverage=SimpleNamespace(model_dump=lambda **kw: {}), evidence_ids=[],
            formal_weights={"TECHNICAL": .3, "FUNDAMENTAL": .3, "SENTIMENT": .25, "MARGIN": .15},
        )

    service = InteractionWorkflowService(orchestration_factory=lambda: SimpleNamespace(decision_shadow=run))
    # Construct the confirmed request directly; route confirmation has its own tests.
    req = DecisionWorkflowRequest.model_construct(**request(data_cutoff=CUTOFF - timedelta(days=1)).model_dump(), parent_request_id=None)
    payload = service.decision(req, request_id="test").formal_result
    assert payload["status"] == "UNAVAILABLE"
    assert payload["score"] is None
    assert payload["final_action"] is None


def _bundle():
    from data_hub.schemas.unified import FactorType
    from trading.research.orchestration.factor_loader import FactorLoader
    from trading.research.orchestration.schemas import FactorView
    from trading.schemas import AnalysisMode

    factors = {}
    for kind in (FactorType.TECHNICAL, FactorType.FUNDAMENTAL):
        factors[kind] = FactorView(
            factor_output_id=kind.value, factor_type=kind, score=-1, confidence=.8,
            shadow_mode=False, generated_at=CUTOFF, data_cutoff=CUTOFF,
            algorithm_version="synthetic", input_snapshot_hash="0" * 64,
            availability_status="AVAILABLE", availability_reason="synthetic",
            evidence_ids=["synthetic:" + kind.value],
        )
    return FactorLoader(object()).build_bundle(
        symbol="600000.SH", analysis_mode=AnalysisMode.DECISION,
        data_cutoff=CUTOFF, factors=factors,
    )


def test_actual_mcp_decision_engine_and_canonical_builder_share_snapshot(snapshot_db):
    from trading.decision_support.canonical_input import CanonicalDecisionInputBuilder, CanonicalVetoContext
    from trading.decision_support.portfolio_context import PortfolioContext
    from trading.decision_support.user_context import UserContext
    from trading.research.orchestration.decision import DecisionShadowEngine

    bundle = _bundle()
    engine = DecisionShadowEngine(SimpleNamespace(load_many=lambda **kw: ([bundle], {})), object())
    formal = engine.run(request()).formal_result
    user = UserContext.finance_mcp_shadow()
    built = CanonicalDecisionInputBuilder(SimpleNamespace(get_holding=lambda *a: None)).build(
        request_id="same-snapshot", user_context=user, request=request(), bundle=bundle,
        market_bars=[], legacy_result=formal,
        portfolio_context=PortfolioContext.research_default(local_user_id=user.local_user_id, as_of=CUTOFF),
        veto_context=CanonicalVetoContext(result=None, source="test", reason="test", input_complete=True),
    )
    assert formal.score == built.decision_input.formal_score == pytest.approx(EXPECTED)
    assert formal.score_source == built.score_source == "cross_section_4d"


def test_trading_decision_service_uses_cutoff_instead_of_latest_snapshot(snapshot_db):
    import asyncio
    from trading.decision_support.orchestrator import TradingDecisionService
    from trading.schemas import Bar, DecisionRequest, PortfolioState

    bars = [Bar(trade_date=date(2025, 1, 1) + timedelta(days=i), open=10+i*.05,
                high=11+i*.05, low=9+i*.05, close=10+i*.05, volume=1000) for i in range(100)]
    req = DecisionRequest(symbol="600000", bars=bars, portfolio=PortfolioState(cash=100000, equity=100000))
    service = TradingDecisionService()
    at_cutoff = asyncio.run(service.decide(req, data_cutoff=CUTOFF))
    latest = asyncio.run(service.decide(req, data_cutoff=CUTOFF + timedelta(days=1)))
    assert at_cutoff.proposal.action.value == "buy"
    assert latest.proposal.action.value == "sell"


@pytest.mark.parametrize("available", [True, False])
def test_intraday_research_cannot_use_legacy_or_forced_legacy_score(snapshot_db, monkeypatch, available):
    import scripts.analyze_stock as analysis
    import scripts.intraday_pipeline as module

    cutoff = CUTOFF if available else CUTOFF - timedelta(days=1)

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz)

    monkeypatch.setenv("HERMES_FORCE_LEGACY_SCORE", "1")
    monkeypatch.setattr(module, "datetime", FixedTime)
    monkeypatch.setattr(analysis, "load_bars", lambda *a: [object()])
    monkeypatch.setattr(analysis, "load_financials", lambda *a: [object()])
    monkeypatch.setattr(analysis, "load_news", lambda *a: [])
    monkeypatch.setattr(analysis, "fundamental_factor", lambda *a: {"score": 1, "confidence": 1})
    monkeypatch.setattr("trading.research.technical.analysis.technical_signal", lambda *a: SimpleNamespace(score=1, confidence=1))
    pipeline = object.__new__(module.IntradayPipeline)
    pipeline._market_regime = lambda: None
    pipeline.pipeline = SimpleNamespace(intraday_strength=lambda *a: {}, swing_quality=lambda *a: {})
    result = pipeline._research_one("600000", {"symbol": "600000.SH"})
    if available:
        assert result["formal_score"] == pytest.approx(EXPECTED)
        assert result["score_source"] == "cross_section_4d"
    else:
        assert result["status"] == "UNAVAILABLE"
        assert result["formal_score"] is None
        assert pipeline.candidate_action_for(result)["action_code"] == "SKIP"


@pytest.mark.parametrize("cutoff_date,expected_text", [("2026-08-12", "0.2900"), ("2026-08-10", "N/A")])
def test_analyze_stock_cli_uses_four_dimensional_snapshot(snapshot_db, monkeypatch, capsys, cutoff_date, expected_text):
    import scripts.analyze_stock as module
    from trading.schemas import Bar

    monkeypatch.setattr(module, "DB", str(snapshot_db))
    monkeypatch.setattr(module.sys, "argv", ["analyze_stock", "600000", "--date", cutoff_date])
    monkeypatch.setattr(module, "load_bars", lambda *a: [Bar(trade_date=date(2026, 8, 10), open=10, high=11, low=9, close=10, volume=100)])
    for name in ("load_financials", "load_news", "load_announcements"):
        monkeypatch.setattr(module, name, lambda *a: [])
    factor = {"score": 1, "confidence": 1, "summary": "test", "risks": [], "events": [], "notes": [], "pv_label": "test"}
    for name in ("technical_factor", "fundamental_factor", "capital_flow_factor", "sentiment_factor", "policy_factor"):
        monkeypatch.setattr(module, name, lambda *a: factor)
    monkeypatch.setattr("trading.research.technical.market_regime.get_market_regime", lambda: None)
    module.main()
    result = capsys.readouterr().out
    assert f"【正式评分 四维】综合分 {expected_text}" in result
    assert "60/40" not in result


def test_evaluation_rejects_mixed_legacy_and_four_dimensional_baselines():
    from dataclasses import replace
    from trading.experiments.parameter_sensitivity import HistoricalParameterObservation, ParameterScenario, ParameterSensitivityService

    observation = HistoricalParameterObservation(
        symbol="600000.SH", signal_date=date(2026, 8, 10), score=.7,
        signal_close=10, atr14=1, entry_zone=(9, 11), future_bars=(),
        point_in_time_valid=True, score_source="cross_section_4d",
    )
    legacy = replace(observation, symbol="000001.SZ", score_source="FORMAL_60_40")
    with pytest.raises(ValueError, match="cannot mix formal scoring baselines"):
        ParameterSensitivityService().evaluate([observation, legacy], ParameterScenario("test", "BASELINE"))
