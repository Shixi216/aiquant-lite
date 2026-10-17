from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from database.migrations.v0119_canonical_shadow_observation_v2 import (
    apply_migration as apply_v0119,
)
from database.migrations.v0120_canonical_shadow_real_observation import (
    apply_migration as apply_v0120,
)
from trading.decision_support.canonical_shadow import (
    CanonicalDecisionShadowRepository,
    DivergenceCategory,
    ShadowRecordSource,
    classify_divergence,
)
from trading.decision_support.canonical_shadow_gate import evaluate_phase3_gate
from trading.research.stock_report_performance import StockReportPerformance


def test_no_fetch_capital_factor_never_calls_external_sources(monkeypatch) -> None:
    import data_hub.services.capital_flow_enhanced as module

    monkeypatch.setattr(
        module,
        "fetch_margin",
        lambda symbol: pytest.fail(f"network margin fetch for {symbol}"),
    )
    monkeypatch.setattr(
        module,
        "fetch_top_list",
        lambda symbol: pytest.fail(f"network top-list fetch for {symbol}"),
    )
    bars = [
        SimpleNamespace(close=10.0, volume=100.0),
        SimpleNamespace(close=10.5, volume=110.0),
    ]

    result = module.capital_flow_factor_enhanced(
        "600172",
        bars,
        allow_external_fetch=False,
    )

    assert result["pv_label"]
    assert result["margin"]["data_count"] == 0
    assert result["top_list"]["data_count"] == 0


def test_no_fetch_stock_report_uses_local_calendar_and_no_ai(monkeypatch) -> None:
    import trading.research.stock_report_service as module

    bar = SimpleNamespace(trade_date=date(2026, 8, 10), close=10.0)
    service = module.StockReportService()
    monkeypatch.setattr(
        service,
        "_load_inputs",
        lambda symbol, performance, cutoff: ([bar], [], [], [], date(2026, 8, 10)),
    )
    monkeypatch.setattr(
        module,
        "_check_freshness",
        lambda symbol, bars: pytest.fail("network calendar path used"),
    )
    monkeypatch.setattr(
        module,
        "fetch_and_persist",
        lambda *args, **kwargs: pytest.fail("fetch pipeline used"),
    )
    monkeypatch.setattr(
        module,
        "sentiment_factor_ai",
        lambda *args, **kwargs: pytest.fail("AI path used"),
    )
    monkeypatch.setattr(
        module,
        "technical_factor",
        lambda bars: {"score": 0.1, "confidence": 0.8, "summary": "ok"},
    )
    monkeypatch.setattr(
        module,
        "fundamental_factor",
        lambda values: {"score": 0.0, "confidence": 0.6, "summary": "ok"},
    )
    monkeypatch.setattr(
        module,
        "capital_flow_factor_enhanced",
        lambda symbol, bars, *, allow_external_fetch: {
            "score": 0.0,
            "pv_label": "local",
            "label": "local",
        }
        if not allow_external_fetch
        else pytest.fail("external capital enabled"),
    )
    monkeypatch.setattr(
        module,
        "sentiment_factor",
        lambda values, cutoff: {"score": 0.0, "events": []},
    )
    monkeypatch.setattr(
        module,
        "policy_factor",
        lambda values: {"score": 0.0, "notes": [], "total_announcements": 0},
    )
    monkeypatch.setattr(
        module,
        "DecisionEngine",
        lambda **kwargs: SimpleNamespace(
            decide=lambda value: SimpleNamespace(
                action_zh="等待",
                execution_status="等待确认",
                next_action="等待",
                current_position_ratio=0.0,
                target_position_ratio=0.0,
                recommended_batches=0,
                frozen_entry_zone=None,
                frozen_preferred_zone=None,
                frozen_stop_loss_price=None,
            )
        ),
    )

    report = service.run(symbol="600172", no_fetch=True, no_ai=True)

    assert "600172" in report
    assert "正式动作(DecisionEngine): 等待" in report


def test_performance_log_emits_slow_markers_without_secrets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import trading.research.stock_report_performance as module

    target = tmp_path / "performance.jsonl"
    monkeypatch.setattr(module, "_performance_log_path", lambda: target)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    performance = StockReportPerformance(
        request_id="request-1",
        symbol="600172",
        mode="RESEARCH",
        no_fetch=True,
        database_owner_pid=123,
    )
    performance.record("db_query_ms", 5_001.0)
    performance._started -= 31.0

    performance.finish()

    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["event"] for row in records] == [
        "STOCK_REPORT_PERFORMANCE",
        "SLOW_STAGE",
        "SLOW_STOCK_REPORT",
    ]
    assert "token" not in target.read_text().casefold()
    assert "api_key" not in target.read_text().casefold()


def _observation(**overrides):
    values = {
        "observation_id": "obs-1",
        "request_id": "request-1",
        "record_source": "TEST",
        "symbol": "600172",
        "local_user_id": "test",
        "external_user_id": "test",
        "channel": "test",
        "user_context_source": "TEST",
        "user_authenticated": False,
        "observed_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
        "data_cutoff": datetime(2026, 8, 13, tzinfo=timezone.utc),
        "snapshot_id": "snapshot-1",
        "legacy_score": 0.5,
        "canonical_score": 0.5,
        "legacy_technical_score": 0.6,
        "legacy_fundamental_score": 0.35,
        "canonical_technical_score": 0.6,
        "canonical_fundamental_score": 0.35,
        "legacy_action": "BUY",
        "canonical_action": "BUY",
        "legacy_veto": False,
        "canonical_veto": False,
        "canonical_pit": True,
        "canonical_data_status": "FRESH",
        "score_source_status": "PIT_FACTOR_BUNDLE_MATCH",
        "veto_input_status": "VETO_INPUT_COMPLETE",
        "veto_source": "HISTORICAL_CANDIDATE_VETO",
        "veto_reason": "complete",
        "veto_event_count": 0,
        "account_context_source": "RESEARCH_DEFAULT_ACCOUNT_CONTEXT",
        "canonical_current_position": 0.0,
        "canonical_target_position": 0.1,
        "canonical_price_zones_json": {},
        "canonical_execution_status": "READY",
        "latency_legacy_ms": 3.0,
        "latency_canonical_ms": 7.0,
        "shadow_overhead_ms": 7.0,
        "legacy_success": True,
        "canonical_success": True,
        "legacy_error": "",
        "canonical_error": "",
        "canonical_error_type": "",
        "action_diverged": False,
        "veto_diverged": False,
        "score_diverged": False,
        "pit_diverged": False,
        "divergence_categories_json": [],
        "divergence_reason": "NONE",
        "score_bucket": "0.4_TO_0.65",
        "research_only": True,
        "affects_legacy_output": False,
        "creates_order": False,
        "creates_trade": False,
        "creates_trade_plan": False,
        "creates_decision_packet": False,
    }
    values.update(overrides)
    return values


def test_shadow_stats_default_to_real_and_exclude_test(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    apply_v0119(connection)
    apply_v0120(connection)

    @contextmanager
    def _connection(*, read_only=False):
        del read_only
        yield connection

    monkeypatch.setattr(
        "trading.decision_support.canonical_shadow.get_connection",
        _connection,
    )
    repository = CanonicalDecisionShadowRepository()
    repository.save(_observation())
    repository.save(
        _observation(
            observation_id="obs-real",
            record_source="REAL",
            user_context_source="UNAUTHENTICATED_FINANCE_MCP_ISOLATION",
        )
    )

    real = repository.stats()
    test = repository.stats(record_source=ShadowRecordSource.TEST)

    assert real["record_source"] == "REAL"
    assert real["total"] == 1
    assert test["total"] == 1
    connection.close()


def test_unknown_divergence_is_explicit() -> None:
    categories = classify_divergence(
        legacy_score=0.5,
        canonical_score=0.5,
        legacy_action="BUY",
        canonical_action="BUY",
        legacy_veto=False,
        canonical_veto=False,
        score_source_mismatch=False,
        canonical_pit=True,
        canonical_data_status="FRESH",
        veto_input_complete=True,
        canonical_current_position=0.0,
        account_context_assumption=False,
        score_diverged=True,
        pit_diverged=False,
    )

    assert DivergenceCategory.UNKNOWN_DIVERGENCE.value in categories


def test_phase3_gate_rejects_insufficient_real_sample() -> None:
    gate = evaluate_phase3_gate(
        {
            "total": 0,
            "canonical_success_rate": 0.0,
            "unknown_divergence": 0,
            "pit_diverged": 0,
            "score_source_mismatch": 0,
            "legacy_failures": 0,
            "trading_days": 0,
        },
        full_test_failures=0,
        full_test_skipped=0,
    )

    assert not gate.eligible
    assert "INSUFFICIENT_REAL_SAMPLE" in gate.reasons
