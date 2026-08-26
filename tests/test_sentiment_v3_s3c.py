from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_DATASET_HASH,
    EXPECTED_DATASET_ID,
)
from trading.research.sentiment.v3.research_shadow import (
    BaselineAvailability,
    CANDIDATE_FEATURES,
    CompositeMethod,
    ConfidenceEffect,
    FEATURE_DIRECTIONS,
    FeatureValueQuality,
    LegacyDecisionReference,
    S3CResearchShadowRunner,
    augment_decision,
    feature_quality,
    make_research_packet,
    risk_reminder_from_values,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _packet(score: float | None = 0.25, confidence: float = 0.75):
    values = {
        "capital.dragon_tiger.institution_net_amount": 1.0,
        "capital.dragon_tiger.net_amount": 2.0,
        "capital.stock.turnover": 3.0,
        "stock.gap_and_follow_through": -0.1,
    }
    qualities = {name: FeatureValueQuality.VALID for name in CANDIDATE_FEATURES}
    return make_research_packet(
        source_packet_id="source-1",symbol="600000.SH",trade_date=date(2025,1,2),
        cutoff=datetime(2025,1,3,9,10,tzinfo=SHANGHAI),
        method=CompositeMethod.RANK_AVERAGE,feature_values=values,
        feature_quality=qualities,lineage={"feature_lineage_hash":"lineage"},
        research_score=score,confidence=confidence,
    )


def test_01_candidate_set_and_direction_are_frozen():
    assert len(CANDIDATE_FEATURES) == 4
    assert set(FEATURE_DIRECTIONS) == set(CANDIDATE_FEATURES)
    assert tuple(FEATURE_DIRECTIONS.values()) == (1,1,-1,-1)


def test_02_packet_is_deterministic_and_keeps_lineage():
    first = _packet()
    second = _packet()
    assert first.research_packet_id == second.research_packet_id
    assert first.content_hash == second.content_hash
    assert first.lineage["feature_lineage_hash"] == "lineage"
    assert first.dataset_id == EXPECTED_DATASET_ID
    assert first.dataset_hash == EXPECTED_DATASET_HASH


def test_03_packet_requires_all_four_candidate_slots():
    packet = _packet()
    values = dict(packet.feature_values)
    values.pop(CANDIDATE_FEATURES[0])
    with pytest.raises(ValueError,match="four frozen candidates"):
        packet.model_copy(update={"feature_values":values}).model_validate(
            packet.model_copy(update={"feature_values":values}).model_dump()
        )


def test_04_missing_dragon_tiger_is_not_zero():
    quality = feature_quality(
        CANDIDATE_FEATURES[0],None,has_dragon_tiger=False,
    )
    assert quality == FeatureValueQuality.NOT_APPLICABLE_NO_DRAGON_TIGER
    assert feature_quality(CANDIDATE_FEATURES[2],None,has_dragon_tiger=False) == (
        FeatureValueQuality.MISSING
    )


def test_05_risk_reminder_rule_is_transparent():
    assert risk_reminder_from_values(
        has_dragon_tiger=True,institution_net_amount=-1.0,net_amount=2.0,
        turnover_percentile=0.8,gap_follow_through_percentile=0.2,
    )
    assert not risk_reminder_from_values(
        has_dragon_tiger=True,institution_net_amount=-1.0,net_amount=2.0,
        turnover_percentile=0.4,gap_follow_through_percentile=0.2,
    )


def test_06_no_legacy_baseline_is_explicit_and_creates_no_action():
    result = augment_decision(_packet(),None,risk_reminder=True)
    assert result.baseline_availability == (
        BaselineAvailability.NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION
    )
    assert result.legacy_action is None and result.shadow_action is None
    assert result.confidence_effect == ConfidenceEffect.NOT_AVAILABLE
    assert result.creates_trade is False


def test_07_shadow_never_changes_legacy_action_or_confidence():
    packet = _packet(score=-0.3)
    legacy = LegacyDecisionReference(
        decision_id="legacy-1",symbol=packet.symbol,cutoff=packet.cutoff,
        action="BUY",confidence=0.8,vetoed=False,
    )
    result = augment_decision(packet,legacy,risk_reminder=True)
    assert result.shadow_action == result.legacy_action == "BUY"
    assert result.shadow_confidence == result.legacy_confidence == 0.8
    assert result.action_changed is False
    assert result.confidence_effect == ConfidenceEffect.DECREASE
    assert result.pre_veto_risk_candidate is True


def test_08_future_legacy_reference_is_rejected():
    packet = _packet()
    future = LegacyDecisionReference(
        decision_id="future",symbol=packet.symbol,
        cutoff=datetime(2025,1,3,9,20,tzinfo=SHANGHAI),action="BUY",
    )
    with pytest.raises(ValueError,match="PIT-compatible"):
        augment_decision(packet,future)


def test_09_score_sql_is_daily_cross_sectional_and_oriented():
    sql = S3CResearchShadowRunner._score_cte()
    assert "PARTITION BY trade_date" in sql
    assert "capital__stock__turnover" in sql
    assert "-1 *" in sql
    source = Path("trading/research/sentiment/v3/research_shadow.py").read_text()
    assert source.count("ORDER BY p.{score_column},p.symbol) q") == 2


def test_10_pit_validation_rejects_future_source(tmp_path: Path):
    database = tmp_path / "bad.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        """
        CREATE TABLE replay_dataset_metadata(
            dataset_id VARCHAR,content_hash VARCHAR,packet_count BIGINT,
            trading_date_count BIGINT,symbol_count BIGINT,date_from DATE,date_to DATE,
            feature_count INTEGER
        );
        CREATE TABLE replay_feature_packets(
            packet_id VARCHAR,record_source VARCHAR,market_available_time TIMESTAMPTZ,
            stock_available_time TIMESTAMPTZ,margin_available_time TIMESTAMPTZ,
            dragon_tiger_available_time TIMESTAMPTZ,decision_cutoff TIMESTAMPTZ,
            feature_trade_date DATE
        );
        INSERT INTO replay_feature_packets VALUES (
            'p','REPLAY','2025-01-03 09:20:00+08','2025-01-03 09:00:00+08',
            '2025-01-03 09:00:00+08','2025-01-02 18:00:00+08',
            '2025-01-03 09:10:00+08','2025-01-02'
        );
        CREATE TABLE replay_labels_wide(
            packet_id VARCHAR,decision_cutoff TIMESTAMPTZ,exec_1d__entry_date DATE
        );
        INSERT INTO replay_labels_wide VALUES
            ('p','2025-01-03 09:10:00+08','2025-01-03');
        """
    )
    connection.execute(
        "INSERT INTO replay_dataset_metadata VALUES (?, ?, 1, 1, 1, '2025-01-02', '2025-01-02', 38)",
        [EXPECTED_DATASET_ID,EXPECTED_DATASET_HASH],
    )
    connection.close()
    runner = S3CResearchShadowRunner(replay_database=database)
    with pytest.raises(ValueError,match="PIT validation failed"):
        runner._validate_replay()


def test_11_models_have_no_production_mutation_fields():
    packet = _packet()
    fields = set(type(packet).model_fields)
    assert not fields & {"formal_score","target_position","order","trade_plan"}
    assert packet.formal_weight == 0
    assert packet.research_only and not packet.creates_trade
