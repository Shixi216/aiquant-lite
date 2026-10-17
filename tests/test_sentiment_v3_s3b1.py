from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_DATASET_HASH,
    EXPECTED_DATASET_ID,
    EXPECTED_EVALUATION_SHA,
    EXPECTED_LABEL_SPEC_SHA,
    EXPECTED_REGISTRY_SHA,
    FeatureClassification,
    LabelType,
    ReplayFeatureEvaluationRunner,
    benjamini_hochberg,
    build_report,
    classify_feature,
)


TZ = ZoneInfo("Asia/Shanghai")
FEATURE = "stock.return_pct"
FEATURE_COLUMN = "stock__return_pct"
FEATURES = [FEATURE] + [f"stock.test_feature_{index}" for index in range(37)]
HORIZONS = (1, 3, 5, 10, 20)


def _create_replay(path: Path, *, leakage: bool = False) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE replay_dataset_metadata (
            dataset_id VARCHAR,content_hash VARCHAR,date_from DATE,date_to DATE,
            packet_count BIGINT,feature_count INTEGER,registry_sha VARCHAR,
            label_spec_sha VARCHAR,evaluation_sha VARCHAR
        );
        CREATE TABLE replay_feature_packets (
            packet_id VARCHAR,symbol VARCHAR,trade_date DATE,decision_cutoff TIMESTAMPTZ,
            record_source VARCHAR,quality_flags_json JSON,
            market_available_time TIMESTAMPTZ,stock_available_time TIMESTAMPTZ,
            margin_available_time TIMESTAMPTZ,dragon_tiger_available_time TIMESTAMPTZ,
            stock__return_pct DOUBLE
        );
        """
    )
    label_columns = []
    for horizon in HORIZONS:
        for kind in ("stat", "exec"):
            prefix = f"{kind}_{horizon}d"
            label_columns.extend(
                [
                    f"{prefix}__adjusted_return DOUBLE",
                    f"{prefix}__mae DOUBLE",
                    f"{prefix}__mfe DOUBLE",
                    f"{prefix}__entry_date DATE",
                    f"{prefix}__exit_date DATE",
                ]
            )
    connection.execute(
        "CREATE TABLE replay_labels_wide (packet_id VARCHAR," + ",".join(label_columns) + ")"
    )
    start = date(2026, 1, 5)
    packet_count = 0
    for day_index in range(3):
        day = start + timedelta(days=day_index)
        cutoff = datetime.combine(day + timedelta(days=1), time(9, 10), tzinfo=TZ)
        available = cutoff - timedelta(minutes=10)
        if leakage and day_index == 0:
            available = cutoff + timedelta(minutes=1)
        for symbol_index in range(25):
            packet_id = f"packet-{day_index}-{symbol_index}"
            value = None if symbol_index == 0 else float(symbol_index)
            connection.execute(
                "INSERT INTO replay_feature_packets VALUES (?,?,?,?,?,'[]',?,?,?,?,?)",
                [
                    packet_id,
                    f"{symbol_index:06d}.SZ",
                    day,
                    cutoff,
                    "REPLAY",
                    available,
                    cutoff - timedelta(minutes=5),
                    cutoff - timedelta(minutes=10),
                    day,
                    value,
                ],
            )
            row: list[object] = [packet_id]
            for horizon in HORIZONS:
                result = (symbol_index - 12) / 1000 * (1 if day_index != 1 else -1)
                exit_day = day + timedelta(days=max(1, horizon))
                row.extend([result, -0.01, 0.02, day, exit_day])
                row.extend([result - 0.0005, -0.012, 0.018, day + timedelta(days=1), exit_day])
            connection.execute(
                "INSERT INTO replay_labels_wide VALUES (" + ",".join("?" for _ in row) + ")",
                row,
            )
            packet_count += 1
    connection.execute(
        "INSERT INTO replay_dataset_metadata VALUES (?,?,?,?,?,?,?,?,?)",
        [
            EXPECTED_DATASET_ID,
            EXPECTED_DATASET_HASH,
            start,
            start + timedelta(days=2),
            packet_count,
            38,
            EXPECTED_REGISTRY_SHA,
            EXPECTED_LABEL_SPEC_SHA,
            EXPECTED_EVALUATION_SHA,
        ],
    )
    connection.close()


def _research_set(path: Path, *, dataset_hash: str = EXPECTED_DATASET_HASH) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset_id": EXPECTED_DATASET_ID,
                "dataset_content_hash": dataset_hash,
                "registry_sha256": EXPECTED_REGISTRY_SHA,
                "label_spec_sha256": EXPECTED_LABEL_SPEC_SHA,
                "evaluation_sha256": EXPECTED_EVALUATION_SHA,
                "feature_names": FEATURES,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def runner(tmp_path: Path) -> ReplayFeatureEvaluationRunner:
    replay = tmp_path / "replay.duckdb"
    research = tmp_path / "research.json"
    _create_replay(replay)
    _research_set(research)
    return ReplayFeatureEvaluationRunner(
        replay_database=replay,
        result_database=tmp_path / "results.duckdb",
        research_set_path=research,
    )


def test_01_ic_is_calculated_per_trade_date_and_cutoff(runner):
    with duckdb.connect(str(runner.replay_database), read_only=True) as connection:
        daily = runner._daily_correlations(
            connection, feature=FEATURE, prefix="stat_1d"
        )
    assert len(daily) == 3
    assert daily[0]["ic"] == pytest.approx(1.0)
    assert daily[1]["ic"] == pytest.approx(-1.0)
    assert daily[2]["rank_ic"] == pytest.approx(1.0)


def test_02_missing_feature_is_not_filled_with_zero(runner):
    with duckdb.connect(str(runner.replay_database), read_only=True) as connection:
        daily = runner._daily_correlations(
            connection, feature=FEATURE, prefix="stat_1d"
        )
    assert {row["sample_count"] for row in daily} == {24}


def test_03_dataset_hash_is_frozen_and_checked(tmp_path: Path):
    replay = tmp_path / "replay.duckdb"
    research = tmp_path / "research.json"
    _create_replay(replay)
    _research_set(research, dataset_hash="0" * 64)
    value = ReplayFeatureEvaluationRunner(replay, tmp_path / "out.duckdb", research)
    with pytest.raises(ValueError, match="dataset hash"):
        value.run(features=[FEATURE], persist=False)


def test_04_pit_leakage_is_rejected_before_evaluation(tmp_path: Path):
    replay = tmp_path / "replay.duckdb"
    research = tmp_path / "research.json"
    _create_replay(replay, leakage=True)
    _research_set(research)
    value = ReplayFeatureEvaluationRunner(replay, tmp_path / "out.duckdb", research)
    with pytest.raises(ValueError, match="PIT/Label invariant"):
        value.run(features=[FEATURE], persist=False)


def test_05_benjamini_hochberg_is_monotone_and_keeps_all_tests():
    adjusted = benjamini_hochberg([(0, 0.01), (1, 0.04), (2, 0.03)])
    assert set(adjusted) == {0, 1, 2}
    assert adjusted[0] <= adjusted[2] <= adjusted[1]


def test_06_end_to_end_results_keep_lineage_and_research_isolation(runner):
    metadata, results, metrics = runner.run(features=[FEATURE], persist=True)
    assert len(results) == 10
    assert metadata.dataset_hash == EXPECTED_DATASET_HASH
    assert metadata.registry_hash == EXPECTED_REGISTRY_SHA
    assert metadata.label_spec_hash == EXPECTED_LABEL_SPEC_SHA
    assert metadata.evaluation_framework_hash == EXPECTED_EVALUATION_SHA
    assert metadata.formal_weight == 0
    assert metadata.alpha_conclusion == "NONE"
    assert metrics["evaluation_seconds"] >= 0
    assert all(item.formal_weight == 0 and item.alpha_conclusion == "NONE" for item in results)
    assert not any(hasattr(item, "action") for item in results)
    connection = duckdb.connect(str(runner.result_database), read_only=True)
    assert connection.execute("SELECT COUNT(*) FROM s3b1_results").fetchone()[0] == 10
    assert connection.execute(
        "SELECT COUNT(*) FROM s3b1_daily_correlations"
    ).fetchone()[0] == 30
    connection.close()


def test_07_result_ids_and_hashes_are_deterministic(runner):
    first, first_results, _ = runner.run(features=[FEATURE], persist=False)
    second, second_results, _ = runner.run(features=[FEATURE], persist=False)
    assert first.evaluation_id == second.evaluation_id
    assert first.content_hash == second.content_hash
    assert [item.content_hash for item in first_results] == [
        item.content_hash for item in second_results
    ]


def test_08_report_contains_no_strategy_or_alpha_conclusion(runner, tmp_path: Path):
    metadata, results, metrics = runner.run(features=[FEATURE], persist=False)
    report = build_report(metadata, results, metrics, tmp_path / "report.md")
    text = report.read_text(encoding="utf-8")
    assert "No formal Alpha conclusion" in text
    assert "formal_weight: 0" in text
    assert "best strategy" not in text.lower()


def test_09_classification_is_rule_based_not_manual(runner):
    _, results, _ = runner.run(features=[FEATURE], persist=False)
    statistical = [item for item in results if item.label_type == LabelType.STATISTICAL]
    classification, flags = classify_feature(statistical)
    assert classification in set(FeatureClassification)
    assert isinstance(flags, list)


def test_10_market_regime_is_explicitly_unavailable(runner):
    _, results, _ = runner.run(features=[FEATURE], persist=False)
    assert {item.market_regime_status for item in results} == {"NOT_AVAILABLE"}

