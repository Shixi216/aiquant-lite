from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from statistics import fmean, median, pstdev
from time import perf_counter
from typing import Any, Literal

import duckdb
from pydantic import Field

from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.historical_replay import default_replay_database
from trading.research.sentiment.v3.historical_replay_build import feature_column
from trading.research.sentiment.v3.labels import label_specs_v1
from trading.research.sentiment.v3.schemas import SnapshotModel


S3B1_ALGORITHM_VERSION = "sentiment-v3-s3b1-evaluation-1.0.0"
S3B1_CLASSIFICATION_VERSION = "SINGLE_FEATURE_CLASSIFICATION_V1"
S3B1_QUALITY_POLICY_VERSION = "EXCLUDE_LOW_COVERAGE_DATES_V1"
S3B1_RESULT_SCHEMA_VERSION = "SENTIMENT_V3_S3B1_RESULT_V1"
EXPECTED_DATASET_ID = "sv3_s3b_replay_222e9321c2719ecc90bfe149cbf394a1"
EXPECTED_DATASET_HASH = (
    "222e9321c2719ecc90bfe149cbf394a138a99fb46d2e7d884669ec39b4f92fa5"
)
EXPECTED_REGISTRY_SHA = (
    "779AD72DEC929AC42FC7A65FB5E3E7A94913C44E8F1010FC63B9181030466080"
)
EXPECTED_LABEL_SPEC_SHA = (
    "49D6BAB04E3548A7A57606B93A3E326797AA3981C7DD1DD296374FDC459EF1D0"
)
EXPECTED_EVALUATION_SHA = (
    "F2D3D2059B63FD4A5FBC6963A9F16EAEC9EBC49D47FEA89D48D94E67C4859138"
)
MULTIPLE_TESTING_COUNT = 38 * 5
MIN_CROSS_SECTION_SIZE = 20
MIN_VALID_IC_DATES = 60
NO_SIGNAL_RANK_IC = 0.005
PROMISING_RANK_IC = 0.01
TAIL_LOSS_RATIO = 0.50


class FeatureClassification(StrEnum):
    PROMISING = "PROMISING"
    WEAK = "WEAK"
    UNSTABLE = "UNSTABLE"
    TAIL_DEPENDENT = "TAIL_DEPENDENT"
    NO_SIGNAL = "NO_SIGNAL"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class LabelType(StrEnum):
    STATISTICAL = "STATISTICAL"
    EXECUTABLE = "EXECUTABLE"


class ReturnGroupStats(SnapshotModel):
    sample_count: int
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    mae_mean: float | None = None
    mfe_mean: float | None = None


class TailRobustnessResult(SnapshotModel):
    excluded_top_pct: int
    valid_dates: int
    ic_mean: float | None
    rank_ic_mean: float | None
    q5_mean_return: float | None
    q5_q1_spread: float | None


class TimeSliceResult(SnapshotModel):
    year: int
    valid_dates: int
    ic_mean: float | None
    rank_ic_mean: float | None
    q5_q1_spread: float | None


class FeatureEvaluationResult(SnapshotModel):
    evaluation_id: str
    dataset_id: str
    dataset_hash: str
    registry_hash: str
    label_spec_hash: str
    evaluation_framework_hash: str
    algorithm_version: str = S3B1_ALGORITHM_VERSION
    classification_version: str = S3B1_CLASSIFICATION_VERSION
    quality_policy_version: str = S3B1_QUALITY_POLICY_VERSION
    feature_name: str
    horizon: int
    label_type: LabelType
    label_name: str
    sample_count: int
    feature_available_count: int
    valid_sample_count: int
    coverage: float
    missing_rate: float
    valid_dates: int
    excluded_low_coverage_dates: int
    pearson_ic: float | None
    rank_ic: float | None
    ic_mean: float | None
    ic_median: float | None
    ic_std: float | None
    ic_ir: float | None
    rank_ic_mean: float | None
    rank_ic_median: float | None
    rank_ic_std: float | None
    rank_ic_ir: float | None
    positive_ratio: float | None
    negative_ratio: float | None
    rank_positive_ratio: float | None
    rank_negative_ratio: float | None
    quantiles: dict[int, ReturnGroupStats]
    q5_q1_spread: float | None
    top_bottom: dict[str, ReturnGroupStats]
    tail_robustness: dict[int, TailRobustnessResult]
    time_slices: dict[int, TimeSliceResult]
    p_value: float | None = None
    fdr_q_value: float | None = None
    multiple_testing_count: int = MULTIPLE_TESTING_COUNT
    market_regime_status: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    classification: FeatureClassification = FeatureClassification.WEAK
    evidence_flags: list[str] = Field(default_factory=list)
    formal_weight: float = 0.0
    alpha_conclusion: str = "NONE"
    research_only: bool = True
    result_schema_version: str = S3B1_RESULT_SCHEMA_VERSION
    content_hash: str = ""


class EvaluationRunMetadata(SnapshotModel):
    evaluation_id: str
    dataset_id: str
    dataset_hash: str
    registry_hash: str
    label_spec_hash: str
    evaluation_framework_hash: str
    algorithm_version: str = S3B1_ALGORITHM_VERSION
    classification_version: str = S3B1_CLASSIFICATION_VERSION
    quality_policy_version: str = S3B1_QUALITY_POLICY_VERSION
    date_from: date
    date_to: date
    feature_count: int
    label_count: int
    result_count: int
    multiple_testing_count: int = MULTIPLE_TESTING_COUNT
    excluded_low_coverage_dates: int
    market_regime_status: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    record_source: Literal["REPLAY"] = "REPLAY"
    external_api_calls: int = 0
    llm_calls: int = 0
    formal_weight: float = 0.0
    alpha_conclusion: str = "NONE"
    created_at: datetime
    content_hash: str


def _stable_hash_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else value
    if isinstance(value, dict):
        return {key: _stable_hash_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stable_hash_value(item) for item in value]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(_stable_hash_value(value)).encode("utf-8")
    ).hexdigest()


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _mean(values: list[float]) -> float | None:
    return None if not values else fmean(values)


def _median(values: list[float]) -> float | None:
    return None if not values else median(values)


def _std(values: list[float]) -> float | None:
    return None if not values else pstdev(values)


def _ir(mean_value: float | None, std_value: float | None) -> float | None:
    if mean_value is None or std_value in (None, 0):
        return None
    return mean_value / std_value


def _sign_ratio(values: list[float], positive: bool) -> float | None:
    if not values:
        return None
    if positive:
        return sum(value > 0 for value in values) / len(values)
    return sum(value < 0 for value in values) / len(values)


def _normal_approx_p_value(values: list[float]) -> float | None:
    """Two-sided normal approximation for the daily IC mean; descriptive only."""
    if len(values) < 2:
        return None
    sample_mean = fmean(values)
    variance = sum((value - sample_mean) ** 2 for value in values) / (len(values) - 1)
    if variance == 0:
        return None
    statistic = sample_mean / math.sqrt(variance / len(values))
    return math.erfc(abs(statistic) / math.sqrt(2.0))


def benjamini_hochberg(p_values: list[tuple[int, float]]) -> dict[int, float]:
    """Return monotone Benjamini-Hochberg q-values without dropping tests."""
    if not p_values:
        return {}
    ordered = sorted(p_values, key=lambda item: (item[1], item[0]))
    total = len(ordered)
    output: dict[int, float] = {}
    running = 1.0
    for reverse_index in range(total - 1, -1, -1):
        key, value = ordered[reverse_index]
        rank = reverse_index + 1
        running = min(running, value * total / rank)
        output[key] = min(1.0, running)
    return output


def classify_feature(
    statistical_results: list[FeatureEvaluationResult],
) -> tuple[FeatureClassification, list[str]]:
    """Versioned conservative classification; thresholds are research-only."""
    usable = [item for item in statistical_results if item.rank_ic_mean is not None]
    flags: list[str] = []
    if len(usable) < 3 or sum(item.valid_dates >= MIN_VALID_IC_DATES for item in usable) < 3:
        return FeatureClassification.INSUFFICIENT_SAMPLE, ["FEWER_THAN_3_USABLE_HORIZONS"]
    directions = {
        1 if item.rank_ic_mean > 0 else -1
        for item in usable
        if abs(item.rank_ic_mean) >= NO_SIGNAL_RANK_IC
    }
    if len(directions) > 1:
        flags.append("CROSS_HORIZON_DIRECTION_REVERSAL")
    tail_dependent = False
    for item in usable:
        tail = item.tail_robustness.get(5)
        if tail is None or tail.rank_ic_mean is None or item.rank_ic_mean == 0:
            continue
        ratio = abs(tail.rank_ic_mean) / abs(item.rank_ic_mean)
        if ratio < TAIL_LOSS_RATIO or tail.rank_ic_mean * item.rank_ic_mean < 0:
            tail_dependent = True
            flags.append(f"TAIL_5PCT_LOSS_H{item.horizon}")
    if tail_dependent:
        return FeatureClassification.TAIL_DEPENDENT, sorted(set(flags))
    if len(directions) > 1:
        return FeatureClassification.UNSTABLE, sorted(set(flags))
    if all(abs(item.rank_ic_mean or 0.0) < NO_SIGNAL_RANK_IC for item in usable):
        return FeatureClassification.NO_SIGNAL, ["ALL_HORIZONS_ABS_RANK_IC_LT_0_005"]
    strong = [item for item in usable if abs(item.rank_ic_mean or 0.0) >= PROMISING_RANK_IC]
    spread_aligned = [
        item for item in strong
        if item.q5_q1_spread is not None
        and item.rank_ic_mean is not None
        and item.q5_q1_spread * item.rank_ic_mean > 0
    ]
    stable_years = 0
    for item in strong:
        aligned = sum(
            row.rank_ic_mean is not None
            and item.rank_ic_mean is not None
            and row.rank_ic_mean * item.rank_ic_mean > 0
            for row in item.time_slices.values()
        )
        stable_years = max(stable_years, aligned)
    if len(strong) >= 3 and len(spread_aligned) >= 3 and stable_years >= 2:
        direction = "POSITIVE_DIRECTION" if next(iter(directions), 1) > 0 else "NEGATIVE_DIRECTION"
        return FeatureClassification.PROMISING, [direction, "MULTI_HORIZON_CONSISTENT"]
    return FeatureClassification.WEAK, sorted(set(flags or ["EVIDENCE_BELOW_PROMISING_RULE"]))


class ReplayFeatureEvaluationRunner:
    """Local-only, strict cross-sectional S3B1 evaluator over the frozen Replay dataset."""

    def __init__(
        self,
        replay_database: Path | str | None = None,
        result_database: Path | str | None = None,
        research_set_path: Path | str = "Sentiment_V3_S3B_Research_Set_V1.json",
    ) -> None:
        self.replay_database = Path(replay_database or default_replay_database())
        self.result_database = Path(
            result_database or self.replay_database.with_name("sentiment_v3_s3b1.duckdb")
        )
        self.research_set_path = Path(research_set_path)

    def _load_contract(self) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        research_set = json.loads(self.research_set_path.read_text(encoding="utf-8"))
        if research_set["dataset_id"] != EXPECTED_DATASET_ID:
            raise ValueError("unexpected Replay dataset ID")
        if research_set["dataset_content_hash"] != EXPECTED_DATASET_HASH:
            raise ValueError("unexpected Replay dataset hash")
        if research_set["registry_sha256"] != EXPECTED_REGISTRY_SHA:
            raise ValueError("Feature Registry SHA changed")
        if research_set["label_spec_sha256"] != EXPECTED_LABEL_SPEC_SHA:
            raise ValueError("Label Spec SHA changed")
        if research_set["evaluation_sha256"] != EXPECTED_EVALUATION_SHA:
            raise ValueError("Evaluation Framework SHA changed")
        features = list(research_set["feature_names"])
        if len(features) != 38 or len(features) != len(set(features)):
            raise ValueError("S3B1 requires exactly 38 unique canonical Replay features")
        connection = duckdb.connect(str(self.replay_database), read_only=True)
        metadata = connection.execute(
            """
            SELECT dataset_id,content_hash,date_from,date_to,packet_count,feature_count,
                   registry_sha,label_spec_sha,evaluation_sha
            FROM replay_dataset_metadata WHERE dataset_id=?
            """,
            [EXPECTED_DATASET_ID],
        ).fetchone()
        leakage_count = connection.execute(
            """
            SELECT COUNT(*) FROM replay_feature_packets p
            JOIN replay_labels_wide l USING(packet_id)
            WHERE p.market_available_time>p.decision_cutoff
               OR p.stock_available_time>p.decision_cutoff
               OR p.margin_available_time>p.decision_cutoff
               OR p.dragon_tiger_available_time>p.decision_cutoff
               OR (l.exec_1d__entry_date IS NOT NULL
                   AND l.exec_1d__entry_date<
                       CAST(p.decision_cutoff AT TIME ZONE 'Asia/Shanghai' AS DATE))
               OR (l.exec_1d__exit_date IS NOT NULL
                   AND l.exec_1d__exit_date<
                       CAST(p.decision_cutoff AT TIME ZONE 'Asia/Shanghai' AS DATE))
               OR (l.stat_1d__exit_date IS NOT NULL
                   AND l.stat_1d__exit_date<
                       CAST(p.decision_cutoff AT TIME ZONE 'Asia/Shanghai' AS DATE))
            """
        ).fetchone()[0]
        connection.close()
        if leakage_count:
            raise ValueError(f"Replay PIT/Label invariant failed: {leakage_count} rows")
        if metadata is None or metadata[1] != EXPECTED_DATASET_HASH:
            raise ValueError("Replay dataset metadata/hash mismatch")
        if metadata[5] != 38:
            raise ValueError("Replay dataset feature count mismatch")
        if tuple(metadata[6:9]) != (
            EXPECTED_REGISTRY_SHA,
            EXPECTED_LABEL_SPEC_SHA,
            EXPECTED_EVALUATION_SHA,
        ):
            raise ValueError("frozen definition SHA mismatch in Replay metadata")
        return research_set, {
            "dataset_id": metadata[0],
            "dataset_hash": metadata[1],
            "date_from": metadata[2],
            "date_to": metadata[3],
            "packet_count": metadata[4],
        }, features

    @staticmethod
    def _evaluation_id(features: list[str]) -> str:
        payload = {
            "dataset_hash": EXPECTED_DATASET_HASH,
            "registry_hash": EXPECTED_REGISTRY_SHA,
            "label_spec_hash": EXPECTED_LABEL_SPEC_SHA,
            "evaluation_framework_hash": EXPECTED_EVALUATION_SHA,
            "algorithm_version": S3B1_ALGORITHM_VERSION,
            "classification_version": S3B1_CLASSIFICATION_VERSION,
            "quality_policy_version": S3B1_QUALITY_POLICY_VERSION,
            "features": features,
            "labels": [item.spec_hash for item in label_specs_v1()],
        }
        return f"sv3_s3b1_{_hash(payload)[:32]}"

    @staticmethod
    def _label_prefix(horizon: int, label_type: LabelType) -> str:
        return f"{'stat' if label_type == LabelType.STATISTICAL else 'exec'}_{horizon}d"

    @staticmethod
    def _daily_correlations(
        connection: duckdb.DuckDBPyConnection,
        *,
        feature: str,
        prefix: str,
    ) -> list[dict[str, Any]]:
        x = _quoted(feature_column(feature))
        y = _quoted(f"{prefix}__adjusted_return")
        return connection.execute(
            f"""
            WITH valid AS (
                SELECT p.trade_date,p.decision_cutoff,p.symbol,p.{x}::DOUBLE AS x,
                       l.{y}::DOUBLE AS y
                FROM replay_feature_packets p
                JOIN replay_labels_wide l USING(packet_id)
                WHERE p.record_source='REPLAY'
                  AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
                  AND p.{x} IS NOT NULL AND l.{y} IS NOT NULL
            ), ranked AS (
                SELECT *,
                    RANK() OVER(PARTITION BY trade_date,decision_cutoff ORDER BY x)
                      +(COUNT(*) OVER(PARTITION BY trade_date,decision_cutoff,x)-1)/2.0 AS rx,
                    RANK() OVER(PARTITION BY trade_date,decision_cutoff ORDER BY y)
                      +(COUNT(*) OVER(PARTITION BY trade_date,decision_cutoff,y)-1)/2.0 AS ry
                FROM valid
            )
            SELECT trade_date,decision_cutoff,COUNT(*) AS sample_count,
                   COUNT(DISTINCT x) AS unique_x,CORR(x,y) AS ic,CORR(rx,ry) AS rank_ic
            FROM ranked GROUP BY trade_date,decision_cutoff ORDER BY trade_date
            """
        ).fetchdf().to_dict("records")

    @staticmethod
    def _group_stats(
        connection: duckdb.DuckDBPyConnection,
        *,
        feature: str,
        prefix: str,
    ) -> tuple[dict[int, ReturnGroupStats], dict[str, ReturnGroupStats], dict[int, float | None]]:
        x = _quoted(feature_column(feature))
        y = _quoted(f"{prefix}__adjusted_return")
        mae = _quoted(f"{prefix}__mae")
        mfe = _quoted(f"{prefix}__mfe")
        quantile_rows = connection.execute(
            f"""
            WITH valid AS (
                SELECT p.trade_date,p.decision_cutoff,p.symbol,p.{x}::DOUBLE x,
                       l.{y}::DOUBLE y,l.{mae}::DOUBLE mae,l.{mfe}::DOUBLE mfe
                FROM replay_feature_packets p JOIN replay_labels_wide l USING(packet_id)
                WHERE p.record_source='REPLAY'
                  AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
                  AND p.{x} IS NOT NULL AND l.{y} IS NOT NULL
            ), counts AS (
                SELECT trade_date,decision_cutoff,COUNT(DISTINCT x) unique_x
                FROM valid GROUP BY trade_date,decision_cutoff
            ), ranked AS (
                SELECT v.*,CUME_DIST() OVER(
                    PARTITION BY v.trade_date,v.decision_cutoff ORDER BY v.x
                ) AS feature_pct
                FROM valid v JOIN counts c USING(trade_date,decision_cutoff)
                WHERE c.unique_x>=5
            ), assigned AS (
                SELECT *,LEAST(5,CEIL(feature_pct*5))::INTEGER q FROM ranked
            )
            SELECT q,COUNT(*),AVG(y),MEDIAN(y),AVG((y>0)::INTEGER),AVG(mae),AVG(mfe)
            FROM assigned GROUP BY q ORDER BY q
            """
        ).fetchall()
        quantiles = {
            int(row[0]): ReturnGroupStats(
                sample_count=row[1],mean_return=row[2],median_return=row[3],
                win_rate=row[4],mae_mean=row[5],mfe_mean=row[6],
            ) for row in quantile_rows
        }
        top_rows = connection.execute(
            f"""
            WITH valid AS (
                SELECT p.trade_date,p.decision_cutoff,p.symbol,p.{x}::DOUBLE x,
                       l.{y}::DOUBLE y,l.{mae}::DOUBLE mae,l.{mfe}::DOUBLE mfe
                FROM replay_feature_packets p JOIN replay_labels_wide l USING(packet_id)
                WHERE p.record_source='REPLAY'
                  AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
                  AND p.{x} IS NOT NULL AND l.{y} IS NOT NULL
            ), ranked AS (
                SELECT *,ROW_NUMBER() OVER(PARTITION BY trade_date,decision_cutoff
                    ORDER BY x,symbol) rn_bottom,
                    ROW_NUMBER() OVER(PARTITION BY trade_date,decision_cutoff
                    ORDER BY x DESC,symbol) rn_top,
                    COUNT(DISTINCT x) OVER(PARTITION BY trade_date,decision_cutoff) unique_x
                FROM valid
            ), selected AS (
                SELECT 'TOP10' bucket,y,mae,mfe FROM ranked WHERE unique_x>=2 AND rn_top<=10
                UNION ALL SELECT 'TOP20',y,mae,mfe FROM ranked WHERE unique_x>=2 AND rn_top<=20
                UNION ALL SELECT 'BOTTOM10',y,mae,mfe FROM ranked WHERE unique_x>=2 AND rn_bottom<=10
                UNION ALL SELECT 'BOTTOM20',y,mae,mfe FROM ranked WHERE unique_x>=2 AND rn_bottom<=20
            )
            SELECT bucket,COUNT(*),AVG(y),MEDIAN(y),AVG((y>0)::INTEGER),AVG(mae),AVG(mfe)
            FROM selected GROUP BY bucket ORDER BY bucket
            """
        ).fetchall()
        top_bottom = {
            row[0]: ReturnGroupStats(
                sample_count=row[1],mean_return=row[2],median_return=row[3],
                win_rate=row[4],mae_mean=row[5],mfe_mean=row[6],
            ) for row in top_rows
        }
        by_year_rows = connection.execute(
            f"""
            WITH valid AS (
                SELECT p.trade_date,p.decision_cutoff,p.{x}::DOUBLE x,l.{y}::DOUBLE y
                FROM replay_feature_packets p JOIN replay_labels_wide l USING(packet_id)
                WHERE p.record_source='REPLAY'
                  AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
                  AND p.{x} IS NOT NULL AND l.{y} IS NOT NULL
            ), counts AS (
                SELECT trade_date,decision_cutoff,COUNT(DISTINCT x) unique_x
                FROM valid GROUP BY trade_date,decision_cutoff
            ), ranked AS (
                SELECT v.*,LEAST(5,CEIL(CUME_DIST() OVER(
                    PARTITION BY v.trade_date,v.decision_cutoff ORDER BY v.x)*5))::INTEGER q
                FROM valid v JOIN counts c USING(trade_date,decision_cutoff)
                WHERE c.unique_x>=5
            )
            SELECT YEAR(trade_date),
                   AVG(y) FILTER(WHERE q=5)-AVG(y) FILTER(WHERE q=1) spread
            FROM ranked GROUP BY YEAR(trade_date) ORDER BY YEAR(trade_date)
            """
        ).fetchall()
        return quantiles, top_bottom, {int(row[0]): row[1] for row in by_year_rows}

    @staticmethod
    def _tail_results(
        connection: duckdb.DuckDBPyConnection,
        *,
        feature: str,
        prefix: str,
    ) -> dict[int, TailRobustnessResult]:
        x = _quoted(feature_column(feature))
        y = _quoted(f"{prefix}__adjusted_return")
        output: dict[int, TailRobustnessResult] = {}
        for percentage in (1, 5, 10):
            rows = connection.execute(
                f"""
                WITH valid AS (
                    SELECT p.trade_date,p.decision_cutoff,p.symbol,p.{x}::DOUBLE x,
                           l.{y}::DOUBLE y
                    FROM replay_feature_packets p JOIN replay_labels_wide l USING(packet_id)
                    WHERE p.record_source='REPLAY'
                      AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
                      AND p.{x} IS NOT NULL AND l.{y} IS NOT NULL
                ), returns_ranked AS (
                    SELECT *,CUME_DIST() OVER(PARTITION BY trade_date,decision_cutoff
                        ORDER BY y DESC) winner_pct FROM valid
                ), kept AS (
                    SELECT * FROM returns_ranked WHERE winner_pct>{percentage/100.0}
                ), ranks AS (
                    SELECT *,RANK() OVER(PARTITION BY trade_date,decision_cutoff ORDER BY x)
                        +(COUNT(*) OVER(PARTITION BY trade_date,decision_cutoff,x)-1)/2.0 rx,
                        RANK() OVER(PARTITION BY trade_date,decision_cutoff ORDER BY y)
                        +(COUNT(*) OVER(PARTITION BY trade_date,decision_cutoff,y)-1)/2.0 ry
                    FROM kept
                ), daily AS (
                    SELECT trade_date,decision_cutoff,COUNT(*) n,COUNT(DISTINCT x) unique_x,
                           CORR(x,y) ic,CORR(rx,ry) rank_ic
                    FROM ranks GROUP BY trade_date,decision_cutoff
                ), counts AS (
                    SELECT trade_date,decision_cutoff,COUNT(DISTINCT x) unique_x
                    FROM kept GROUP BY trade_date,decision_cutoff
                ), q AS (
                    SELECT k.*,LEAST(5,CEIL(CUME_DIST() OVER(
                        PARTITION BY k.trade_date,k.decision_cutoff ORDER BY k.x)*5))::INTEGER q
                    FROM kept k JOIN counts c USING(trade_date,decision_cutoff)
                    WHERE c.unique_x>=5
                )
                SELECT
                    (SELECT AVG(ic) FROM daily WHERE n>={MIN_CROSS_SECTION_SIZE} AND unique_x>=2),
                    (SELECT AVG(rank_ic) FROM daily WHERE n>={MIN_CROSS_SECTION_SIZE} AND unique_x>=2),
                    (SELECT COUNT(*) FROM daily WHERE n>={MIN_CROSS_SECTION_SIZE} AND unique_x>=2),
                    (SELECT AVG(y) FROM q WHERE q=5),
                    (SELECT AVG(y) FILTER(WHERE q=5)-AVG(y) FILTER(WHERE q=1) FROM q)
                """
            ).fetchone()
            output[percentage] = TailRobustnessResult(
                excluded_top_pct=percentage,ic_mean=rows[0],rank_ic_mean=rows[1],
                valid_dates=rows[2],q5_mean_return=rows[3],q5_q1_spread=rows[4],
            )
        return output

    def _evaluate_one(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        evaluation_id: str,
        feature: str,
        horizon: int,
        label_type: LabelType,
        label_name: str,
        label_spec_hash: str,
        dataset: dict[str, Any],
        excluded_dates: int,
    ) -> tuple[FeatureEvaluationResult, list[dict[str, Any]]]:
        prefix = self._label_prefix(horizon, label_type)
        x = _quoted(feature_column(feature))
        y = _quoted(f"{prefix}__adjusted_return")
        counts = connection.execute(
            f"""
            SELECT COUNT(*),COUNT(p.{x}),COUNT(*) FILTER(
                WHERE p.{x} IS NOT NULL AND l.{y} IS NOT NULL)
            FROM replay_feature_packets p JOIN replay_labels_wide l USING(packet_id)
            WHERE p.record_source='REPLAY'
              AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
            """
        ).fetchone()
        daily = self._daily_correlations(connection, feature=feature, prefix=prefix)
        valid_daily = [
            item for item in daily
            if item["sample_count"] >= MIN_CROSS_SECTION_SIZE
            and item["unique_x"] >= 2
            and item["ic"] is not None
            and item["rank_ic"] is not None
        ]
        ic_values = [float(item["ic"]) for item in valid_daily]
        rank_values = [float(item["rank_ic"]) for item in valid_daily]
        if valid_daily:
            quantiles, top_bottom, year_spreads = self._group_stats(
                connection, feature=feature, prefix=prefix
            )
        else:
            quantiles, top_bottom, year_spreads = {}, {}, {}
        years: dict[int, TimeSliceResult] = {}
        for year in (2024, 2025, 2026):
            rows = [item for item in valid_daily if item["trade_date"].year == year]
            years[year] = TimeSliceResult(
                year=year,valid_dates=len(rows),
                ic_mean=_mean([float(item["ic"]) for item in rows]),
                rank_ic_mean=_mean([float(item["rank_ic"]) for item in rows]),
                q5_q1_spread=year_spreads.get(year),
            )
        tails = (
            self._tail_results(connection, feature=feature, prefix=prefix)
            if valid_daily and label_type == LabelType.STATISTICAL else {}
        )
        ic_mean = _mean(ic_values)
        ic_std = _std(ic_values)
        rank_mean = _mean(rank_values)
        rank_std = _std(rank_values)
        payload = {
            "evaluation_id": evaluation_id,"dataset_id": dataset["dataset_id"],
            "dataset_hash": dataset["dataset_hash"],"registry_hash": EXPECTED_REGISTRY_SHA,
            "label_spec_hash": label_spec_hash,"evaluation_framework_hash": EXPECTED_EVALUATION_SHA,
            "feature_name": feature,"horizon": horizon,"label_type": label_type.value,
            "label_name": label_name,"sample_count": counts[0],
            "feature_available_count": counts[1],"valid_sample_count": counts[2],
            "valid_dates": len(valid_daily),"daily": valid_daily,"quantiles": quantiles,
            "top_bottom": top_bottom,"tails": tails,"years": years,
        }
        result = FeatureEvaluationResult(
            evaluation_id=evaluation_id,dataset_id=dataset["dataset_id"],
            dataset_hash=dataset["dataset_hash"],registry_hash=EXPECTED_REGISTRY_SHA,
            label_spec_hash=label_spec_hash,evaluation_framework_hash=EXPECTED_EVALUATION_SHA,
            feature_name=feature,horizon=horizon,label_type=label_type,label_name=label_name,
            sample_count=counts[0],feature_available_count=counts[1],
            valid_sample_count=counts[2],coverage=counts[2]/counts[0],
            missing_rate=1-counts[1]/counts[0],valid_dates=len(valid_daily),
            excluded_low_coverage_dates=excluded_dates,
            pearson_ic=ic_mean,rank_ic=rank_mean,ic_mean=ic_mean,
            ic_median=_median(ic_values),ic_std=ic_std,ic_ir=_ir(ic_mean,ic_std),
            rank_ic_mean=rank_mean,rank_ic_median=_median(rank_values),
            rank_ic_std=rank_std,rank_ic_ir=_ir(rank_mean,rank_std),
            positive_ratio=_sign_ratio(ic_values,True),negative_ratio=_sign_ratio(ic_values,False),
            rank_positive_ratio=_sign_ratio(rank_values,True),
            rank_negative_ratio=_sign_ratio(rank_values,False),quantiles=quantiles,
            q5_q1_spread=(quantiles.get(5).mean_return-quantiles.get(1).mean_return)
                if quantiles.get(5) and quantiles.get(1)
                and quantiles.get(5).mean_return is not None
                and quantiles.get(1).mean_return is not None else None,
            top_bottom=top_bottom,tail_robustness=tails,time_slices=years,
            p_value=_normal_approx_p_value(rank_values),content_hash=_hash(payload),
        )
        return result, daily

    @staticmethod
    def _create_result_schema(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS s3b1_runs (
                evaluation_id VARCHAR PRIMARY KEY,metadata_json JSON NOT NULL,
                content_hash VARCHAR NOT NULL,created_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS s3b1_results (
                evaluation_id VARCHAR NOT NULL,feature_name VARCHAR NOT NULL,
                label_type VARCHAR NOT NULL,horizon INTEGER NOT NULL,label_name VARCHAR NOT NULL,
                valid_sample_count BIGINT NOT NULL,coverage DOUBLE NOT NULL,missing_rate DOUBLE NOT NULL,
                ic_mean DOUBLE,rank_ic_mean DOUBLE,ic_ir DOUBLE,rank_ic_ir DOUBLE,
                q5_q1_spread DOUBLE,p_value DOUBLE,fdr_q_value DOUBLE,
                classification VARCHAR NOT NULL,evidence_flags_json JSON NOT NULL,
                result_json JSON NOT NULL,content_hash VARCHAR NOT NULL,
                PRIMARY KEY(evaluation_id,feature_name,label_type,horizon)
            );
            CREATE TABLE IF NOT EXISTS s3b1_daily_correlations (
                evaluation_id VARCHAR NOT NULL,feature_name VARCHAR NOT NULL,
                label_type VARCHAR NOT NULL,horizon INTEGER NOT NULL,
                trade_date DATE NOT NULL,cutoff TIMESTAMPTZ NOT NULL,
                sample_count INTEGER NOT NULL,unique_feature_values INTEGER NOT NULL,
                ic DOUBLE,rank_ic DOUBLE,
                PRIMARY KEY(evaluation_id,feature_name,label_type,horizon,trade_date,cutoff)
            );
            """
        )

    def run(
        self,
        *,
        features: list[str] | None = None,
        persist: bool = True,
    ) -> tuple[EvaluationRunMetadata, list[FeatureEvaluationResult], dict[str, float]]:
        started = perf_counter()
        _, dataset, frozen_features = self._load_contract()
        selected = frozen_features if features is None else features
        unknown = set(selected) - set(frozen_features)
        if unknown:
            raise ValueError(f"features outside S3B_RESEARCH_SET_V1: {sorted(unknown)}")
        evaluation_id = self._evaluation_id(selected)
        connection = duckdb.connect(str(self.replay_database), read_only=True)
        connection.execute("SET threads=1")
        connection.execute("PRAGMA disable_progress_bar")
        excluded_dates = connection.execute(
            """
            SELECT COUNT(DISTINCT trade_date) FROM replay_feature_packets
            WHERE CAST(quality_flags_json AS VARCHAR) LIKE '%LOW_COVERAGE%'
            """
        ).fetchone()[0]
        specs = {item.label_name: item for item in label_specs_v1()}
        results: list[FeatureEvaluationResult] = []
        daily_rows: list[dict[str, Any]] = []
        evaluation_started = perf_counter()
        for feature_index, feature in enumerate(selected, start=1):
            for horizon in (1, 3, 5, 10, 20):
                for label_type in (LabelType.STATISTICAL, LabelType.EXECUTABLE):
                    label_name = (
                        f"CLOSE_TO_CLOSE_{horizon}D"
                        if label_type == LabelType.STATISTICAL
                        else f"NEXT_OPEN_TO_CLOSE_{horizon}D"
                    )
                    result, daily = self._evaluate_one(
                        connection,evaluation_id=evaluation_id,feature=feature,
                        horizon=horizon,label_type=label_type,label_name=label_name,
                        label_spec_hash=specs[label_name].spec_hash,dataset=dataset,
                        excluded_dates=excluded_dates,
                    )
                    results.append(result)
                    for item in daily:
                        daily_rows.append({
                            "evaluation_id":evaluation_id,"feature_name":feature,
                            "label_type":label_type.value,"horizon":horizon,
                            "trade_date":item["trade_date"],"cutoff":item["decision_cutoff"],
                            "sample_count":item["sample_count"],
                            "unique_feature_values":item["unique_x"],
                            "ic":item["ic"],"rank_ic":item["rank_ic"],
                        })
            print(f"S3B1 feature {feature_index}/{len(selected)}: {feature}", flush=True)
        connection.close()
        statistical_indices = [
            index for index,item in enumerate(results)
            if item.label_type == LabelType.STATISTICAL
        ]
        q_values = benjamini_hochberg(
            [
                (index,results[index].p_value)
                if results[index].p_value is not None else (index,1.0)
                for index in statistical_indices
            ]
        )
        for index,q_value in q_values.items():
            results[index]=results[index].model_copy(update={"fdr_q_value":q_value})
        grouped: dict[str,list[FeatureEvaluationResult]] = defaultdict(list)
        for item in results:
            if item.label_type == LabelType.STATISTICAL:
                grouped[item.feature_name].append(item)
        classifications: dict[str,tuple[FeatureClassification,list[str]]] = {
            feature:classify_feature(items) for feature,items in grouped.items()
        }
        for index,item in enumerate(results):
            classification,evidence_flags=classifications[item.feature_name]
            updated=item.model_copy(update={
                "classification":classification,"evidence_flags":evidence_flags,
            })
            results[index]=updated.model_copy(update={
                "content_hash":_hash(updated.model_dump(
                    mode="json",exclude={"content_hash"}
                ))
            })
        now=datetime.now().astimezone()
        metadata_payload={
            "evaluation_id":evaluation_id,"dataset_id":dataset["dataset_id"],
            "dataset_hash":dataset["dataset_hash"],"registry_hash":EXPECTED_REGISTRY_SHA,
            "label_spec_hash":EXPECTED_LABEL_SPEC_SHA,
            "evaluation_framework_hash":EXPECTED_EVALUATION_SHA,
            "algorithm_version":S3B1_ALGORITHM_VERSION,
            "classification_version":S3B1_CLASSIFICATION_VERSION,
            "quality_policy_version":S3B1_QUALITY_POLICY_VERSION,
            "date_from":dataset["date_from"],"date_to":dataset["date_to"],
            "features":selected,"labels":[item.spec_hash for item in label_specs_v1()],
            "result_hashes":sorted(item.content_hash for item in results),
            "excluded_low_coverage_dates":excluded_dates,
        }
        metadata=EvaluationRunMetadata(
            evaluation_id=evaluation_id,dataset_id=dataset["dataset_id"],
            dataset_hash=dataset["dataset_hash"],registry_hash=EXPECTED_REGISTRY_SHA,
            label_spec_hash=EXPECTED_LABEL_SPEC_SHA,
            evaluation_framework_hash=EXPECTED_EVALUATION_SHA,
            date_from=dataset["date_from"],date_to=dataset["date_to"],
            feature_count=len(selected),label_count=10,result_count=len(results),
            excluded_low_coverage_dates=excluded_dates,created_at=now,
            content_hash=_hash(metadata_payload),
        )
        write_started=perf_counter()
        if persist:
            target=duckdb.connect(str(self.result_database))
            self._create_result_schema(target)
            target.execute("DELETE FROM s3b1_daily_correlations WHERE evaluation_id=?",[evaluation_id])
            target.execute("DELETE FROM s3b1_results WHERE evaluation_id=?",[evaluation_id])
            target.execute("DELETE FROM s3b1_runs WHERE evaluation_id=?",[evaluation_id])
            target.execute(
                "INSERT INTO s3b1_runs VALUES (?,?,?,?)",
                [evaluation_id,json.dumps(metadata.model_dump(mode="json"),default=str),
                 metadata.content_hash,now],
            )
            result_rows=[]
            for item in results:
                result_rows.append([
                    evaluation_id,item.feature_name,item.label_type.value,item.horizon,
                    item.label_name,item.valid_sample_count,item.coverage,item.missing_rate,
                    item.ic_mean,item.rank_ic_mean,item.ic_ir,item.rank_ic_ir,
                    item.q5_q1_spread,item.p_value,item.fdr_q_value,item.classification.value,
                    json.dumps(item.evidence_flags),
                    json.dumps(item.model_dump(mode="json"),default=str),item.content_hash,
                ])
            target.executemany(
                "INSERT INTO s3b1_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                result_rows,
            )
            if daily_rows:
                target.executemany(
                    "INSERT INTO s3b1_daily_correlations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [[row[key] for key in (
                        "evaluation_id","feature_name","label_type","horizon","trade_date",
                        "cutoff","sample_count","unique_feature_values","ic","rank_ic",
                    )] for row in daily_rows],
                )
            target.close()
        metrics={
            "evaluation_seconds":write_started-evaluation_started,
            "result_write_seconds":perf_counter()-write_started,
            "total_seconds":perf_counter()-started,
            "result_count":float(len(results)),
        }
        return metadata,results,metrics


def build_report(
    metadata: EvaluationRunMetadata,
    results: list[FeatureEvaluationResult],
    metrics: dict[str,float],
    output_path: Path | str,
) -> Path:
    by_feature: dict[str,list[FeatureEvaluationResult]]=defaultdict(list)
    for item in results:
        by_feature[item.feature_name].append(item)
    summaries=[]
    for feature,items in sorted(by_feature.items()):
        statistical={item.horizon:item for item in items if item.label_type==LabelType.STATISTICAL}
        classification=items[0].classification.value
        ranks=" / ".join(
            "NA" if statistical[h].rank_ic_mean is None else f"{statistical[h].rank_ic_mean:.5f}"
            for h in (1,3,5,10,20)
        )
        spreads=" / ".join(
            "NA" if statistical[h].q5_q1_spread is None else f"{statistical[h].q5_q1_spread:.5f}"
            for h in (1,3,5,10,20)
        )
        coverage=fmean(item.coverage for item in items)
        summaries.append((feature,classification,coverage,ranks,spreads,", ".join(items[0].evidence_flags)))
    ranked_rank_ic=sorted(
        summaries,
        key=lambda row:max(
            [abs(item.rank_ic_mean) for item in by_feature[row[0]]
             if item.label_type==LabelType.STATISTICAL and item.rank_ic_mean is not None] or [-1]
        ),reverse=True,
    )
    ranked_ic=sorted(
        summaries,
        key=lambda row:max(
            [abs(item.ic_mean) for item in by_feature[row[0]]
             if item.label_type==LabelType.STATISTICAL and item.ic_mean is not None] or [-1]
        ),reverse=True,
    )
    counts=defaultdict(int)
    for _,classification,*_ in summaries:
        counts[classification]+=1
    lines=[
        "# Sentiment V3 S3B1 Evaluation Report","",
        "## 1. Dataset","",
        f"- Evaluation ID: {metadata.evaluation_id}",
        f"- Dataset: {metadata.dataset_id}",f"- Dataset hash: {metadata.dataset_hash}",
        f"- Date range: {metadata.date_from} to {metadata.date_to}",
        f"- Features: {metadata.feature_count}","- Labels: 5 Statistical + 5 Executable",
        f"- Results: {metadata.result_count}",
        f"- Excluded LOW_COVERAGE dates: {metadata.excluded_low_coverage_dates}",
        "- IC method: daily trade_date + cutoff cross-sectional Pearson",
        "- RankIC method: daily trade_date + cutoff cross-sectional Spearman",
        f"- Multiple tests: {metadata.multiple_testing_count}; BH-FDR retained, no result deleted",
        "- Market regime: NOT_AVAILABLE; no regime was invented","",
        "## 2. Feature evaluation table","",
        "RankIC and spread columns are 1D / 3D / 5D / 10D / 20D Statistical results.","",
        "| Feature | Classification | Mean coverage | RankIC | Q5-Q1 spread | Evidence |",
        "|---|---|---:|---|---|---|",
    ]
    for row in summaries:
        lines.append(f"| {row[0]} | {row[1]} | {row[2]:.2%} | {row[3]} | {row[4]} | {row[5] or '-'} |")
    lines += ["","## 3. IC / RankIC research ordering","",
              "Ordering is for audit browsing only; it is not a strategy ranking or weight.",
              "","### 3.1 Pearson IC ordering","",
              "| Order | Feature | Classification | IC 1/3/5/10/20 |","|---:|---|---|---|"]
    for index,row in enumerate(ranked_ic,start=1):
        statistical={
            item.horizon:item for item in by_feature[row[0]]
            if item.label_type==LabelType.STATISTICAL
        }
        values=" / ".join(
            "NA" if statistical[h].ic_mean is None else f"{statistical[h].ic_mean:.5f}"
            for h in (1,3,5,10,20)
        )
        lines.append(f"| {index} | {row[0]} | {row[1]} | {values} |")
    lines += ["","### 3.2 RankIC ordering","",
              "| Order | Feature | Classification | RankIC 1/3/5/10/20 |","|---:|---|---|---|"]
    for index,row in enumerate(ranked_rank_ic,start=1):
        lines.append(f"| {index} | {row[0]} | {row[1]} | {row[3]} |")
    lines += ["","## 4. Classification counts",""]
    for name in FeatureClassification:
        lines.append(f"- {name.value}: {counts[name.value]}")
    lines += ["","## 5. Stable research candidates","",
              ", ".join(row[0] for row in summaries if row[1]=="PROMISING") or "None.",
              "","## 6. Failed or insufficient features","",
              ", ".join(f"{row[0]} ({row[1]})" for row in summaries
                        if row[1] in {"NO_SIGNAL","INSUFFICIENT_SAMPLE"}) or "None.",
              "","## 7. Risk notes","",
              "- PROMISING is a research classification, not an Alpha or production conclusion.",
              "- Market-level daily features are constant inside each daily stock cross-section; strict IC therefore reports zero variance instead of substituting a time-series correlation.",
              "- CURRENT_MEMBERSHIP_ONLY and PIT-unsafe features are absent.",
              "- Vendor-derived data must not be interpreted as verified institution flow.",
              "- Statistical and Executable results remain separate.",
              "- Statistical-label tail tests remove each date's top 1%, 5%, and 10% winners and recompute daily IC/RankIC and Q5 spread.",
              "- FDR uses a documented normal approximation to the daily RankIC mean because SciPy is not a project dependency.",
              "","## 8. Isolation and performance","",
              f"- Evaluation time: {metrics['evaluation_seconds']:.3f}s",
              f"- Result DB write time: {metrics['result_write_seconds']:.3f}s",
              "- External API calls: 0","- LLM calls: 0","- formal_weight: 0",
              "- DecisionEngine, 60/40, A/B Shadow and Canonical Shadow unchanged.",
              "","## 9. Conclusion","",
              "No formal Alpha conclusion, strategy, prediction, BUY/SELL/HOLD, or production weight is produced.",]
    path=Path(output_path)
    path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return path


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(description="Sentiment V3 S3B1 local Replay evaluation")
    parser.add_argument("--replay-database",type=Path,default=default_replay_database())
    parser.add_argument("--result-database",type=Path)
    parser.add_argument("--research-set",type=Path,default=Path("Sentiment_V3_S3B_Research_Set_V1.json"))
    parser.add_argument("--report",type=Path,default=Path("Sentiment_V3_S3B1_Evaluation_Report.md"))
    parser.add_argument("--feature",action="append")
    parser.add_argument("--no-persist",action="store_true")
    return parser


def main(argv: list[str] | None=None) -> int:
    args=_parser().parse_args(argv)
    runner=ReplayFeatureEvaluationRunner(
        replay_database=args.replay_database,result_database=args.result_database,
        research_set_path=args.research_set,
    )
    metadata,results,metrics=runner.run(features=args.feature,persist=not args.no_persist)
    build_report(metadata,results,metrics,args.report)
    print(json.dumps({"metadata":metadata.model_dump(mode="json"),"metrics":metrics,
                      "report":str(args.report),"external_api_calls":0,"llm_calls":0,
                      "formal_weight":0,"alpha_conclusion":"NONE"},default=str,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__=[
    "EvaluationRunMetadata","FeatureClassification","FeatureEvaluationResult",
    "LabelType","ReplayFeatureEvaluationRunner","S3B1_ALGORITHM_VERSION",
    "S3B1_CLASSIFICATION_VERSION","S3B1_QUALITY_POLICY_VERSION",
    "benjamini_hochberg","build_report","classify_feature","main",
]
