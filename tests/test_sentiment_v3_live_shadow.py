from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from database.migrations.v0121_sentiment_v3_live_shadow import apply_migration
from trading.research.sentiment.v3.live_shadow import (
    LiveShadowExecutor,
    LiveShadowResult,
    ShadowWindow,
    RiskReminderStatus,
    compute_equal_weight_composite,
    evaluate_risk_reminder,
    FEATURE_DIRECTIONS
)
from trading.research.sentiment.v3.shadow_drift_monitor import ShadowDriftMonitor

@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    apply_migration(c)
    return c

def test_live_shadow_result_safety_invariants():
    cutoff = datetime.now(timezone(timedelta(hours=8)))
    res = LiveShadowResult(
        observation_id="1", run_id="1", symbol="000001.SZ", trade_date=date(2023, 1, 1),
        cutoff=cutoff, window=ShadowWindow.PREOPEN, research_score=0.5, confidence=1.0,
        risk_reminder_triggered=False, risk_reminder_details={}, feature_values={},
        feature_quality={}, provider_latency_ms=10.0, data_coverage_ratio=1.0,
        content_hash="hash"
    )
    assert res.formal_weight == 0
    assert res.research_only is True
    assert res.creates_trade is False

def test_equal_weight_composite_with_all_features():
    features = [
        {"capital.dragon_tiger.institution_net_amount": 1.0, "capital.dragon_tiger.net_amount": 1.0, "capital.stock.turnover": 0.05, "stock.gap_and_follow_through": 0.01},
        {"capital.dragon_tiger.institution_net_amount": -1.0, "capital.dragon_tiger.net_amount": -1.0, "capital.stock.turnover": 0.15, "stock.gap_and_follow_through": -0.01}
    ]
    scores = compute_equal_weight_composite(features, FEATURE_DIRECTIONS)
    assert len(scores) == 2
    assert scores[0] is not None
    assert scores[1] is not None
    assert scores[0] > scores[1]

def test_equal_weight_composite_with_missing_dragon_tiger():
    features = [
        {"capital.stock.turnover": 0.05, "stock.gap_and_follow_through": 0.01},
        {"capital.stock.turnover": 0.15, "stock.gap_and_follow_through": -0.01}
    ]
    scores = compute_equal_weight_composite(features, FEATURE_DIRECTIONS)
    assert len(scores) == 2

def test_risk_reminder_triggered():
    vals = {"capital.dragon_tiger.net_amount": -10, "capital.stock.turnover": 0.5, "stock.gap_and_follow_through": -0.5}
    medians = {"capital.dragon_tiger.net_amount": 0.0, "capital.stock.turnover": 0.1, "stock.gap_and_follow_through": 0.0}
    status, details = evaluate_risk_reminder(vals, medians)
    assert status == RiskReminderStatus.TRIGGERED

def test_risk_reminder_not_triggered():
    vals = {"capital.dragon_tiger.net_amount": 10, "capital.stock.turnover": 0.05, "stock.gap_and_follow_through": 0.5}
    medians = {"capital.dragon_tiger.net_amount": 0.0, "capital.stock.turnover": 0.1, "stock.gap_and_follow_through": 0.0}
    status, details = evaluate_risk_reminder(vals, medians)
    assert status == RiskReminderStatus.NOT_TRIGGERED

def test_risk_reminder_insufficient_data():
    vals = {"capital.dragon_tiger.net_amount": 10}
    medians = {"capital.dragon_tiger.net_amount": 0.0, "capital.stock.turnover": 0.1, "stock.gap_and_follow_through": 0.0}
    status, details = evaluate_risk_reminder(vals, medians)
    assert status == RiskReminderStatus.INSUFFICIENT_DATA

def test_content_hash_deterministic(conn):
    executor = LiveShadowExecutor(conn)
    cutoff = datetime(2023, 1, 1, tzinfo=timezone.utc)
    h1 = executor._get_content_hash("000001.SZ", date(2023, 1, 1), cutoff, {"a": 1})
    h2 = executor._get_content_hash("000001.SZ", date(2023, 1, 1), cutoff, {"a": 1})
    assert h1 == h2

def test_content_hash_changes_with_input(conn):
    executor = LiveShadowExecutor(conn)
    cutoff = datetime(2023, 1, 1, tzinfo=timezone.utc)
    h1 = executor._get_content_hash("000001.SZ", date(2023, 1, 1), cutoff, {"a": 1})
    h2 = executor._get_content_hash("000002.SZ", date(2023, 1, 1), cutoff, {"a": 1})
    assert h1 != h2

def test_persist_observation_roundtrip(conn):
    executor = LiveShadowExecutor(conn)
    cutoff = datetime.now(timezone(timedelta(hours=8)))
    executor.run_single("000001.SZ", date(2023, 1, 1), cutoff, ShadowWindow.PREOPEN, {}, {}, "run1", 0.5)
    rows = conn.execute("SELECT symbol FROM sentiment_v3_live_shadow_observations").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "000001.SZ"

def test_drift_normal(conn):
    monitor = ShadowDriftMonitor(conn)
    rep = monitor.compute_drift(date(2023, 1, 1), "PREOPEN", "feat", [1.0, 1.1], [1.0, 1.1])
    assert rep.drift_status == "NORMAL"

def test_drift_warning(conn):
    monitor = ShadowDriftMonitor(conn)
    rep = monitor.compute_drift(date(2023, 1, 1), "PREOPEN", "feat", [5.0, 5.1], [1.0, 1.1])
    assert rep.drift_status in ["WARNING", "ALERT"] # Will be ALERT with large zscore actually, wait: mean1=5.05, mean2=1.05. std2 is ~0.07. diff=4. z=4/0.07=57 -> ALERT

def test_drift_alert(conn):
    monitor = ShadowDriftMonitor(conn)
    rep = monitor.compute_drift(date(2023, 1, 1), "PREOPEN", "feat", [10.0, 11.0], [1.0, 1.1])
    assert rep.drift_status == "ALERT"
    
def test_drift_warning_exact(conn):
    monitor = ShadowDriftMonitor(conn)
    rep = monitor.compute_drift(date(2023, 1, 1), "PREOPEN", "feat", [1.14, 1.24], [1.0, 1.1])
    # diff = 0.14. std = 0.0707. z = 2.0 -> WARNING
    assert rep.drift_status == "WARNING"

def test_migration_creates_tables(conn):
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    assert "sentiment_v3_live_shadow_observations" in tables
    assert "sentiment_v3_drift_reports" in tables

def test_run_summary_safety_invariants(conn):
    executor = LiveShadowExecutor(conn)
    cutoff = datetime.now(timezone(timedelta(hours=8)))
    batch = [{"symbol": "000001.SZ", "feature_values": {}}]
    summary = executor.run_batch(date(2023, 1, 1), cutoff, ShadowWindow.PREOPEN, batch)
    assert summary.formal_weight == 0
    assert summary.research_only is True
    assert summary.creates_trade is False
