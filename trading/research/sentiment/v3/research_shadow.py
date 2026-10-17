from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import duckdb
from pydantic import Field, field_validator, model_validator

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_DATASET_HASH,
    EXPECTED_DATASET_ID,
    EXPECTED_EVALUATION_SHA,
    EXPECTED_LABEL_SPEC_SHA,
    EXPECTED_REGISTRY_SHA,
    MIN_CROSS_SECTION_SIZE,
)
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.historical_replay import default_replay_database
from trading.research.sentiment.v3.schemas import RecordSource, SnapshotModel


S3C_ALGORITHM_VERSION = "sentiment-v3-s3c-research-shadow-1.0.0"
S3C_PACKET_SCHEMA_VERSION = "SENTIMENT_RESEARCH_PACKET_V1"
S3C_COMPOSITE_VERSION = "S3C_COMPOSITE_ORIENTED_V1"
S3C_RISK_RULE_VERSION = "S3C_RISK_REMINDER_MEDIAN_V1"
S3C_RESULT_VERSION = "SENTIMENT_V3_S3C_RESULT_V1"
S3B2_RUN_ID = "sv3_s3b2_e9cb6272813d0ec6b5a346d7b286699f"
S3B2_CONTENT_HASH = "6c1d5b1fb22fa1d0b1eec9f2066ccea7a118c820b6f811cfffec0c1d1473cfae"

CANDIDATE_FEATURES = (
    "capital.dragon_tiger.institution_net_amount",
    "capital.dragon_tiger.net_amount",
    "capital.stock.turnover",
    "stock.gap_and_follow_through",
)

# Frozen from S3B2's cross-horizon direction audit. These signs orient the
# research composite; they are not production weights and are never optimized.
FEATURE_DIRECTIONS = {
    "capital.dragon_tiger.institution_net_amount": 1,
    "capital.dragon_tiger.net_amount": 1,
    "capital.stock.turnover": -1,
    "stock.gap_and_follow_through": -1,
}

FEATURE_FAMILIES = {
    "dragon_tiger": CANDIDATE_FEATURES[:2],
    "trading_behavior": CANDIDATE_FEATURES[2:],
}

FEATURE_COLUMNS = {
    "capital.dragon_tiger.institution_net_amount": (
        "capital__dragon_tiger__institution_net_amount"
    ),
    "capital.dragon_tiger.net_amount": "capital__dragon_tiger__net_amount",
    "capital.stock.turnover": "capital__stock__turnover",
    "stock.gap_and_follow_through": "stock__gap_and_follow_through",
}


class CompositeMethod(StrEnum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    RANK_AVERAGE = "RANK_AVERAGE"
    FEATURE_FAMILY_AVERAGE = "FEATURE_FAMILY_AVERAGE"


class FeatureValueQuality(StrEnum):
    VALID = "VALID"
    NOT_APPLICABLE_NO_DRAGON_TIGER = "NOT_APPLICABLE_NO_DRAGON_TIGER"
    MISSING = "MISSING"


class ConfidenceEffect(StrEnum):
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    NEUTRAL = "NEUTRAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class BaselineAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION = (
        "NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION"
    )


class SentimentResearchPacket(SnapshotModel):
    research_packet_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_packet_id: str
    dataset_id: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    trade_date: date
    cutoff: datetime
    method: CompositeMethod
    feature_values: dict[str, float | None]
    feature_quality: dict[str, FeatureValueQuality]
    lineage: dict[str, str]
    research_score: float | None
    confidence: float = Field(ge=0.0, le=1.0)
    record_source: Literal[RecordSource.REPLAY] = RecordSource.REPLAY
    composite_version: str = S3C_COMPOSITE_VERSION
    packet_schema_version: str = S3C_PACKET_SCHEMA_VERSION
    formal_weight: float = 0.0
    research_only: bool = True
    creates_trade: bool = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("cutoff")
    @classmethod
    def cutoff_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cutoff must include timezone")
        return value

    @model_validator(mode="after")
    def candidate_contract_is_complete(self) -> "SentimentResearchPacket":
        expected = set(CANDIDATE_FEATURES)
        if set(self.feature_values) != expected or set(self.feature_quality) != expected:
            raise ValueError("S3C packet must contain the four frozen candidates")
        return self


class LegacyDecisionReference(SnapshotModel):
    decision_id: str
    symbol: str
    cutoff: datetime
    action: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    vetoed: bool | None = None


class DecisionAugmentationShadowResult(SnapshotModel):
    research_packet_id: str
    baseline_availability: BaselineAvailability
    legacy_decision_id: str | None
    legacy_action: str | None
    shadow_action: str | None
    action_changed: Literal[False] = False
    legacy_confidence: float | None
    shadow_confidence: float | None
    confidence_effect: ConfidenceEffect
    research_support_index: float | None
    risk_reminder: bool
    pre_veto_risk_candidate: bool | None
    research_only: Literal[True] = True
    creates_trade: Literal[False] = False
    affects_decision_engine: Literal[False] = False


@dataclass(frozen=True, slots=True)
class CompositeEvaluationMetric:
    method: str
    label_type: str
    horizon: int
    sample_count: int
    valid_dates: int
    coverage: float
    ic_mean: float | None
    rank_ic_mean: float | None
    q5_q1_spread: float | None
    q5_mean_return: float | None
    q1_mean_return: float | None


@dataclass(frozen=True, slots=True)
class S3CShadowRunResult:
    run_id: str
    algorithm_version: str
    source_s3b2_run_id: str
    dataset_id: str
    dataset_hash: str
    candidate_features: tuple[str, ...]
    packet_count: int
    date_count: int
    symbol_count: int
    method_coverage: dict[str, float]
    mean_research_confidence: float
    risk_reminder_count: int
    baseline_status: str
    market_regime_status: str
    evaluation_metric_count: int
    timings: dict[str, float]
    content_hash: str


def _stable_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _stable_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return value


def _hash(value: Any) -> str:
    payload = canonical_json(_stable_value(value)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_research_packet(
    *,
    source_packet_id: str,
    symbol: str,
    trade_date: date,
    cutoff: datetime,
    method: CompositeMethod,
    feature_values: dict[str, float | None],
    feature_quality: dict[str, FeatureValueQuality],
    lineage: dict[str, str],
    research_score: float | None,
    confidence: float,
    dataset_id: str = EXPECTED_DATASET_ID,
    dataset_hash: str = EXPECTED_DATASET_HASH,
) -> SentimentResearchPacket:
    identity = {
        "source_packet_id": source_packet_id,
        "method": method.value,
        "composite_version": S3C_COMPOSITE_VERSION,
        "dataset_hash": dataset_hash,
    }
    packet_id = _hash(identity)
    content = {
        **identity,
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "cutoff": cutoff.isoformat(),
        "feature_values": feature_values,
        "feature_quality": {
            key: value.value for key, value in feature_quality.items()
        },
        "lineage": lineage,
        "research_score": research_score,
        "confidence": confidence,
    }
    return SentimentResearchPacket(
        research_packet_id=packet_id,
        source_packet_id=source_packet_id,
        dataset_id=dataset_id,
        dataset_hash=dataset_hash,
        symbol=symbol,
        trade_date=trade_date,
        cutoff=cutoff,
        method=method,
        feature_values=feature_values,
        feature_quality=feature_quality,
        lineage=lineage,
        research_score=research_score,
        confidence=confidence,
        content_hash=_hash(content),
    )


def research_support_index(packet: SentimentResearchPacket) -> float | None:
    if packet.research_score is None:
        return None
    return packet.research_score * packet.confidence


def augment_decision(
    packet: SentimentResearchPacket,
    legacy: LegacyDecisionReference | None,
    *,
    risk_reminder: bool = False,
) -> DecisionAugmentationShadowResult:
    support = research_support_index(packet)
    if legacy is None:
        return DecisionAugmentationShadowResult(
            research_packet_id=packet.research_packet_id,
            baseline_availability=(
                BaselineAvailability.NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION
            ),
            legacy_decision_id=None,
            legacy_action=None,
            shadow_action=None,
            legacy_confidence=None,
            shadow_confidence=None,
            confidence_effect=ConfidenceEffect.NOT_AVAILABLE,
            research_support_index=support,
            risk_reminder=risk_reminder,
            pre_veto_risk_candidate=None,
        )
    if legacy.symbol != packet.symbol or legacy.cutoff > packet.cutoff:
        raise ValueError("legacy decision is not PIT-compatible with research packet")
    if support is None or support == 0:
        effect = ConfidenceEffect.NEUTRAL
    elif support > 0:
        effect = ConfidenceEffect.INCREASE
    else:
        effect = ConfidenceEffect.DECREASE
    return DecisionAugmentationShadowResult(
        research_packet_id=packet.research_packet_id,
        baseline_availability=BaselineAvailability.AVAILABLE,
        legacy_decision_id=legacy.decision_id,
        legacy_action=legacy.action,
        shadow_action=legacy.action,
        legacy_confidence=legacy.confidence,
        shadow_confidence=legacy.confidence,
        confidence_effect=effect,
        research_support_index=support,
        risk_reminder=risk_reminder,
        pre_veto_risk_candidate=(risk_reminder and legacy.vetoed is False),
    )


def feature_quality(
    feature_name: str,
    value: float | None,
    *,
    has_dragon_tiger: bool | None,
) -> FeatureValueQuality:
    if value is not None:
        return FeatureValueQuality.VALID
    if feature_name.startswith("capital.dragon_tiger.") and has_dragon_tiger is False:
        return FeatureValueQuality.NOT_APPLICABLE_NO_DRAGON_TIGER
    return FeatureValueQuality.MISSING


def risk_reminder_from_values(
    *,
    has_dragon_tiger: bool | None,
    institution_net_amount: float | None,
    net_amount: float | None,
    turnover_percentile: float | None,
    gap_follow_through_percentile: float | None,
) -> bool:
    outflow = any(
        value is not None and value < 0
        for value in (institution_net_amount, net_amount)
    )
    return bool(
        has_dragon_tiger is True
        and outflow
        and turnover_percentile is not None
        and turnover_percentile > 0.5
        and gap_follow_through_percentile is not None
        and gap_follow_through_percentile < 0.5
    )


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class S3CResearchShadowRunner:
    """Local-only S3C composite and sidecar shadow over the frozen Replay dataset."""

    def __init__(
        self,
        *,
        replay_database: Path | str | None = None,
        output_database: Path | str = Path("database/sentiment_v3_s3c.duckdb"),
        s3b2_results: Path | str = Path("Sentiment_V3_S3B2_Research_Results.json"),
    ) -> None:
        self.replay_database = Path(replay_database or default_replay_database())
        self.output_database = Path(output_database)
        self.s3b2_results = Path(s3b2_results)

    def _load_contract(self) -> dict[str, Any]:
        payload = json.loads(self.s3b2_results.read_text(encoding="utf-8"))
        metadata = payload["metadata"]
        checks = {
            "run_id": S3B2_RUN_ID,
            "content_hash": S3B2_CONTENT_HASH,
            "dataset_id": EXPECTED_DATASET_ID,
            "dataset_hash": EXPECTED_DATASET_HASH,
            "registry_hash": EXPECTED_REGISTRY_SHA,
            "label_spec_hash": EXPECTED_LABEL_SPEC_SHA,
            "evaluation_framework_hash": EXPECTED_EVALUATION_SHA,
        }
        for key, expected in checks.items():
            if metadata.get(key) != expected:
                raise ValueError(f"frozen S3B2 contract changed: {key}")
        if tuple(metadata.get("candidate_features", ())) != CANDIDATE_FEATURES:
            raise ValueError("S3C candidate feature set changed")
        if metadata.get("market_regime_status") != "NOT_AVAILABLE":
            raise ValueError("unexpected market regime contract")
        return metadata

    def _validate_replay(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.replay_database), read_only=True)
        try:
            metadata = connection.execute(
                """
                SELECT dataset_id,content_hash,packet_count,trading_date_count,
                       symbol_count,date_from,date_to,feature_count
                FROM replay_dataset_metadata WHERE dataset_id=?
                """,
                [EXPECTED_DATASET_ID],
            ).fetchone()
            if metadata is None:
                raise ValueError("frozen Replay dataset is unavailable")
            if metadata[1] != EXPECTED_DATASET_HASH or metadata[7] != 38:
                raise ValueError("Replay dataset/hash changed")
            pit_errors = connection.execute(
                """
                SELECT COUNT(*)
                FROM replay_feature_packets p
                JOIN replay_labels_wide l USING(packet_id)
                WHERE p.record_source <> 'REPLAY'
                   OR p.market_available_time > p.decision_cutoff
                   OR p.stock_available_time > p.decision_cutoff
                   OR p.margin_available_time > p.decision_cutoff
                   OR p.dragon_tiger_available_time > p.decision_cutoff
                   OR l.decision_cutoff <> p.decision_cutoff
                   OR l.exec_1d__entry_date <= p.feature_trade_date
                   OR EXTRACT(HOUR FROM p.decision_cutoff) > 9
                   OR (EXTRACT(HOUR FROM p.decision_cutoff) = 9
                       AND EXTRACT(MINUTE FROM p.decision_cutoff) >= 30)
                """
            ).fetchone()[0]
            if pit_errors:
                raise ValueError(f"S3C PIT validation failed: {pit_errors}")
            return {
                "packet_count": metadata[2],
                "date_count": metadata[3],
                "symbol_count": metadata[4],
                "date_from": metadata[5],
                "date_to": metadata[6],
            }
        finally:
            connection.close()

    @staticmethod
    def _score_cte() -> str:
        columns = {name: _quoted(column) for name, column in FEATURE_COLUMNS.items()}
        aliases = {
            "capital.dragon_tiger.institution_net_amount": "inst",
            "capital.dragon_tiger.net_amount": "dt_net",
            "capital.stock.turnover": "turnover",
            "stock.gap_and_follow_through": "gap_follow",
        }
        normalized: list[str] = []
        for feature_name, alias in aliases.items():
            column = columns[feature_name]
            direction = FEATURE_DIRECTIONS[feature_name]
            normalized.extend([
                (
                    f"CASE WHEN {column} IS NOT NULL THEN {direction} * "
                    f"({column} - AVG({column}) OVER(PARTITION BY trade_date)) / "
                    f"NULLIF(STDDEV_POP({column}) OVER(PARTITION BY trade_date),0) END "
                    f"AS {alias}_z"
                ),
                (
                    f"CASE WHEN {column} IS NOT NULL THEN {direction} * "
                    f"(2.0 * (RANK() OVER(PARTITION BY trade_date ORDER BY {column} NULLS LAST)-1) / "
                    f"NULLIF(COUNT({column}) OVER(PARTITION BY trade_date)-1,0) - 1.0) END "
                    f"AS {alias}_rank"
                ),
            ])
        return ",\n".join(normalized)

    def _materialize_packets(self, connection: duckdb.DuckDBPyConnection) -> None:
        score_cte = self._score_cte()
        connection.execute("DROP TABLE IF EXISTS sentiment_research_packets_wide")
        connection.execute(
            f"""
            CREATE TABLE sentiment_research_packets_wide AS
            WITH base AS (
                SELECT p.*,
                       {score_cte}
                FROM replay.replay_feature_packets p
                WHERE p.record_source='REPLAY'
                  AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
            ), scored AS (
                SELECT *,
                    ((inst_z IS NOT NULL)::INTEGER + (dt_net_z IS NOT NULL)::INTEGER +
                     (turnover_z IS NOT NULL)::INTEGER + (gap_follow_z IS NOT NULL)::INTEGER)
                        AS observed_count,
                    CASE WHEN observed_count >= 2 THEN
                        (COALESCE(inst_z,0)+COALESCE(dt_net_z,0)+COALESCE(turnover_z,0)+
                         COALESCE(gap_follow_z,0))/observed_count END AS equal_weight_score,
                    CASE WHEN observed_count >= 2 THEN
                        (COALESCE(inst_rank,0)+COALESCE(dt_net_rank,0)+
                         COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0))/observed_count
                    END AS rank_average_score,
                    CASE WHEN (inst_rank IS NOT NULL OR dt_net_rank IS NOT NULL)
                              AND (turnover_rank IS NOT NULL OR gap_follow_rank IS NOT NULL)
                         THEN (
                            (COALESCE(inst_rank,0)+COALESCE(dt_net_rank,0)) /
                            ((inst_rank IS NOT NULL)::INTEGER+(dt_net_rank IS NOT NULL)::INTEGER)
                            +
                            (COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0)) /
                            ((turnover_rank IS NOT NULL)::INTEGER+
                             (gap_follow_rank IS NOT NULL)::INTEGER)
                         ) / 2.0 END AS feature_family_average_score,
                    CASE WHEN capital__dragon_tiger__has_dragon_tiger IS TRUE
                              AND (COALESCE(capital__dragon_tiger__institution_net_amount,0)<0
                                   OR COALESCE(capital__dragon_tiger__net_amount,0)<0)
                              AND turnover_rank < 0
                              AND gap_follow_rank > 0
                         THEN TRUE ELSE FALSE END AS risk_reminder
                FROM base
            )
            SELECT
                SHA256(packet_id || '|' || '{S3C_COMPOSITE_VERSION}') AS research_packet_id,
                packet_id AS source_packet_id,symbol,trade_date,decision_cutoff AS cutoff,
                record_source,registry_content_hash,research_set_hash,feature_lineage_hash,
                market_snapshot_id,stock_snapshot_id,margin_snapshot_id,
                dragon_tiger_snapshot_id,
                capital__dragon_tiger__has_dragon_tiger AS has_dragon_tiger,
                capital__dragon_tiger__institution_net_amount AS institution_net_amount,
                capital__dragon_tiger__net_amount AS dragon_tiger_net_amount,
                capital__stock__turnover AS stock_turnover,
                stock__gap_and_follow_through AS gap_and_follow_through,
                CASE WHEN capital__dragon_tiger__institution_net_amount IS NOT NULL THEN 'VALID'
                     WHEN capital__dragon_tiger__has_dragon_tiger IS FALSE
                     THEN 'NOT_APPLICABLE_NO_DRAGON_TIGER' ELSE 'MISSING' END
                     AS institution_net_amount_quality,
                CASE WHEN capital__dragon_tiger__net_amount IS NOT NULL THEN 'VALID'
                     WHEN capital__dragon_tiger__has_dragon_tiger IS FALSE
                     THEN 'NOT_APPLICABLE_NO_DRAGON_TIGER' ELSE 'MISSING' END
                     AS dragon_tiger_net_amount_quality,
                CASE WHEN capital__stock__turnover IS NOT NULL THEN 'VALID' ELSE 'MISSING' END
                     AS stock_turnover_quality,
                CASE WHEN stock__gap_and_follow_through IS NOT NULL THEN 'VALID'
                     ELSE 'MISSING' END AS gap_and_follow_through_quality,
                equal_weight_score,rank_average_score,feature_family_average_score,
                observed_count/4.0 AS research_confidence,
                risk_reminder,
                '{S3C_COMPOSITE_VERSION}' AS composite_version,
                '{S3C_PACKET_SCHEMA_VERSION}' AS packet_schema_version,
                0 AS formal_weight,TRUE AS research_only,FALSE AS creates_trade,
                SHA256(packet_id || '|' || '{S3C_COMPOSITE_VERSION}' || '|' ||
                    COALESCE(CAST(equal_weight_score AS VARCHAR),'NULL') || '|' ||
                    COALESCE(CAST(rank_average_score AS VARCHAR),'NULL') || '|' ||
                    COALESCE(CAST(feature_family_average_score AS VARCHAR),'NULL')) AS content_hash
            FROM scored
            """
        )

    @staticmethod
    def _score_columns() -> dict[str, str]:
        return {
            CompositeMethod.EQUAL_WEIGHT.value: "equal_weight_score",
            CompositeMethod.RANK_AVERAGE.value: "rank_average_score",
            CompositeMethod.FEATURE_FAMILY_AVERAGE.value: (
                "feature_family_average_score"
            ),
        }

    def _evaluate(
        self,
        connection: duckdb.DuckDBPyConnection,
    ) -> list[CompositeEvaluationMetric]:
        connection.execute("DROP TABLE IF EXISTS shadow_composite_metrics")
        connection.execute(
            """
            CREATE TABLE shadow_composite_metrics(
                method VARCHAR,label_type VARCHAR,horizon INTEGER,sample_count BIGINT,
                valid_dates BIGINT,coverage DOUBLE,ic_mean DOUBLE,rank_ic_mean DOUBLE,
                q5_q1_spread DOUBLE,q5_mean_return DOUBLE,q1_mean_return DOUBLE
            )
            """
        )
        metrics: list[CompositeEvaluationMetric] = []
        total_packets = connection.execute(
            "SELECT COUNT(*) FROM sentiment_research_packets_wide"
        ).fetchone()[0]
        for method, score_column in self._score_columns().items():
            for label_type, prefix in (("STATISTICAL", "stat"), ("EXECUTABLE", "exec")):
                for horizon in (1, 3, 5, 10, 20):
                    return_column = f"{prefix}_{horizon}d__adjusted_return"
                    status_column = f"{prefix}_{horizon}d__status"
                    row = connection.execute(
                        f"""
                        WITH source AS (
                            SELECT p.trade_date,p.{score_column} x,l.{return_column} y,
                                   RANK() OVER(PARTITION BY p.trade_date ORDER BY p.{score_column}) rx,
                                   RANK() OVER(PARTITION BY p.trade_date ORDER BY l.{return_column}) ry,
                                   NTILE(5) OVER(PARTITION BY p.trade_date
                                                 ORDER BY p.{score_column},p.symbol) q
                            FROM sentiment_research_packets_wide p
                            JOIN replay.replay_labels_wide l
                              ON l.packet_id=p.source_packet_id
                            WHERE p.{score_column} IS NOT NULL
                              AND l.{status_column}='COMPLETE'
                              AND l.{return_column} IS NOT NULL
                        ), daily AS (
                            SELECT trade_date,COUNT(*) n,CORR(x,y) ic,CORR(rx,ry) rank_ic,
                                   AVG(y) FILTER(WHERE q=5)-AVG(y) FILTER(WHERE q=1) spread,
                                   AVG(y) FILTER(WHERE q=5) q5_mean,
                                   AVG(y) FILTER(WHERE q=1) q1_mean
                            FROM source GROUP BY trade_date
                        )
                        SELECT (SELECT COUNT(*) FROM source),
                               COUNT(*) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                               (SELECT COUNT(*) FROM source)/?::DOUBLE,
                               AVG(ic) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                               AVG(rank_ic) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                               AVG(spread) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                               AVG(q5_mean) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                               AVG(q1_mean) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE})
                        FROM daily
                        """,
                        [total_packets],
                    ).fetchone()
                    metric = CompositeEvaluationMetric(
                        method=method,label_type=label_type,horizon=horizon,
                        sample_count=row[0],valid_dates=row[1],coverage=row[2] or 0.0,
                        ic_mean=row[3],rank_ic_mean=row[4],q5_q1_spread=row[5],
                        q5_mean_return=row[6],q1_mean_return=row[7],
                    )
                    metrics.append(metric)
                    connection.execute(
                        "INSERT INTO shadow_composite_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        list(asdict(metric).values()),
                    )
        return metrics

    def _time_metrics(self, connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS shadow_time_metrics")
        connection.execute(
            """
            CREATE TABLE shadow_time_metrics(
                period_type VARCHAR,period VARCHAR,method VARCHAR,horizon INTEGER,
                sample_count BIGINT,valid_dates BIGINT,rank_ic_mean DOUBLE,
                q5_q1_spread DOUBLE
            )
            """
        )
        for method, score_column in self._score_columns().items():
            for horizon in (1, 3, 5, 10, 20):
                for period_type, expression in (
                    ("YEAR", "CAST(YEAR(p.trade_date) AS VARCHAR)"),
                    ("MONTH", "STRFTIME(p.trade_date,'%Y-%m')"),
                ):
                    connection.execute(
                        f"""
                        INSERT INTO shadow_time_metrics
                        WITH source AS (
                            SELECT {expression} period,p.trade_date,p.{score_column} x,
                                   l.exec_{horizon}d__adjusted_return y,
                                   RANK() OVER(PARTITION BY p.trade_date
                                               ORDER BY p.{score_column}) rx,
                                   RANK() OVER(PARTITION BY p.trade_date
                                               ORDER BY l.exec_{horizon}d__adjusted_return) ry,
                                   NTILE(5) OVER(PARTITION BY p.trade_date
                                                 ORDER BY p.{score_column},p.symbol) q
                            FROM sentiment_research_packets_wide p
                            JOIN replay.replay_labels_wide l
                              ON l.packet_id=p.source_packet_id
                            WHERE p.{score_column} IS NOT NULL
                              AND l.exec_{horizon}d__status='COMPLETE'
                              AND l.exec_{horizon}d__adjusted_return IS NOT NULL
                        ), daily AS (
                            SELECT period,trade_date,COUNT(*) n,CORR(rx,ry) rank_ic,
                                AVG(y) FILTER(WHERE q=5)-AVG(y) FILTER(WHERE q=1) spread
                            FROM source GROUP BY period,trade_date
                        )
                        SELECT '{period_type}',period,'{method}',{horizon},SUM(n),COUNT(*),
                               AVG(rank_ic),AVG(spread)
                        FROM daily WHERE n>={MIN_CROSS_SECTION_SIZE} GROUP BY period
                        """
                    )

    def _risk_metrics(self, connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS shadow_risk_metrics")
        connection.execute(
            """
            CREATE TABLE shadow_risk_metrics(
                horizon INTEGER,reminder_count BIGINT,valid_label_count BIGINT,
                reminder_mean_return DOUBLE,non_reminder_mean_return DOUBLE,
                return_difference DOUBLE
            )
            """
        )
        for horizon in (1, 3, 5, 10, 20):
            connection.execute(
                f"""
                INSERT INTO shadow_risk_metrics
                SELECT {horizon},COUNT(*) FILTER(WHERE p.risk_reminder),
                       COUNT(*) FILTER(WHERE p.risk_reminder AND l.exec_{horizon}d__status='COMPLETE'),
                       AVG(l.exec_{horizon}d__adjusted_return)
                           FILTER(WHERE p.risk_reminder AND l.exec_{horizon}d__status='COMPLETE'),
                       AVG(l.exec_{horizon}d__adjusted_return)
                           FILTER(WHERE NOT p.risk_reminder AND l.exec_{horizon}d__status='COMPLETE'),
                       AVG(l.exec_{horizon}d__adjusted_return)
                           FILTER(WHERE p.risk_reminder AND l.exec_{horizon}d__status='COMPLETE')
                       - AVG(l.exec_{horizon}d__adjusted_return)
                           FILTER(WHERE NOT p.risk_reminder AND l.exec_{horizon}d__status='COMPLETE')
                FROM sentiment_research_packets_wide p
                JOIN replay.replay_labels_wide l ON l.packet_id=p.source_packet_id
                """
            )

    def run(self) -> tuple[S3CShadowRunResult, list[CompositeEvaluationMetric]]:
        started = perf_counter()
        contract_started = perf_counter()
        self._load_contract()
        self._validate_replay()
        contract_seconds = perf_counter() - contract_started

        self.output_database.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.output_database))
        try:
            replay_path = str(self.replay_database.resolve()).replace("'", "''")
            connection.execute(f"ATTACH '{replay_path}' AS replay (READ_ONLY)")
            packet_started = perf_counter()
            self._materialize_packets(connection)
            packet_seconds = perf_counter() - packet_started

            evaluation_started = perf_counter()
            metrics = self._evaluate(connection)
            evaluation_seconds = perf_counter() - evaluation_started

            time_started = perf_counter()
            self._time_metrics(connection)
            time_seconds = perf_counter() - time_started

            risk_started = perf_counter()
            self._risk_metrics(connection)
            risk_seconds = perf_counter() - risk_started

            row = connection.execute(
                """
                SELECT COUNT(*),COUNT(DISTINCT trade_date),COUNT(DISTINCT symbol),
                       AVG(research_confidence),COUNT(*) FILTER(WHERE risk_reminder),
                       AVG((equal_weight_score IS NOT NULL)::INTEGER),
                       AVG((rank_average_score IS NOT NULL)::INTEGER),
                       AVG((feature_family_average_score IS NOT NULL)::INTEGER)
                FROM sentiment_research_packets_wide
                """
            ).fetchone()
            timings = {
                "contract_seconds": contract_seconds,
                "packet_materialization_seconds": packet_seconds,
                "composite_evaluation_seconds": evaluation_seconds,
                "time_slice_seconds": time_seconds,
                "risk_audit_seconds": risk_seconds,
                "total_seconds": perf_counter() - started,
            }
            identity = {
                "algorithm_version": S3C_ALGORITHM_VERSION,
                "source_s3b2_run_id": S3B2_RUN_ID,
                "dataset_hash": EXPECTED_DATASET_HASH,
                "candidate_features": CANDIDATE_FEATURES,
            }
            run_id = "sv3_s3c_" + _hash(identity)[:32]
            body = {
                **identity,
                "packet_count": row[0],
                "date_count": row[1],
                "symbol_count": row[2],
                "method_coverage": {
                    CompositeMethod.EQUAL_WEIGHT.value: row[5],
                    CompositeMethod.RANK_AVERAGE.value: row[6],
                    CompositeMethod.FEATURE_FAMILY_AVERAGE.value: row[7],
                },
                "mean_research_confidence": row[3],
                "risk_reminder_count": row[4],
                "baseline_status": (
                    BaselineAvailability.NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION.value
                ),
                "market_regime_status": "NOT_AVAILABLE",
                "evaluation_metrics": [asdict(item) for item in metrics],
            }
            result = S3CShadowRunResult(
                run_id=run_id,
                algorithm_version=S3C_ALGORITHM_VERSION,
                source_s3b2_run_id=S3B2_RUN_ID,
                dataset_id=EXPECTED_DATASET_ID,
                dataset_hash=EXPECTED_DATASET_HASH,
                candidate_features=CANDIDATE_FEATURES,
                packet_count=row[0],date_count=row[1],symbol_count=row[2],
                method_coverage=body["method_coverage"],
                mean_research_confidence=row[3] or 0.0,
                risk_reminder_count=row[4],
                baseline_status=body["baseline_status"],
                market_regime_status="NOT_AVAILABLE",
                evaluation_metric_count=len(metrics),timings=timings,
                content_hash=_hash(body),
            )
            connection.execute("DROP TABLE IF EXISTS shadow_run_metadata")
            connection.execute(
                """
                CREATE TABLE shadow_run_metadata(
                    run_id VARCHAR,algorithm_version VARCHAR,source_s3b2_run_id VARCHAR,
                    dataset_id VARCHAR,dataset_hash VARCHAR,candidate_features_json JSON,
                    packet_count BIGINT,date_count BIGINT,symbol_count BIGINT,
                    method_coverage_json JSON,mean_research_confidence DOUBLE,
                    risk_reminder_count BIGINT,baseline_status VARCHAR,
                    market_regime_status VARCHAR,evaluation_metric_count INTEGER,
                    timings_json JSON,content_hash VARCHAR,record_source VARCHAR,
                    formal_weight INTEGER,research_only BOOLEAN,external_api_calls INTEGER,
                    llm_calls INTEGER,creates_trade BOOLEAN,created_at TIMESTAMPTZ
                )
                """
            )
            connection.execute(
                "INSERT INTO shadow_run_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'REPLAY',0,TRUE,0,0,FALSE,NOW())",
                [
                    result.run_id,result.algorithm_version,result.source_s3b2_run_id,
                    result.dataset_id,result.dataset_hash,json.dumps(result.candidate_features),
                    result.packet_count,result.date_count,result.symbol_count,
                    json.dumps(result.method_coverage),result.mean_research_confidence,
                    result.risk_reminder_count,result.baseline_status,
                    result.market_regime_status,result.evaluation_metric_count,
                    json.dumps(result.timings),result.content_hash,
                ],
            )
            connection.execute("CHECKPOINT")
            return result, metrics
        finally:
            connection.close()


def write_results(
    path: Path,
    result: S3CShadowRunResult,
    metrics: list[CompositeEvaluationMetric],
    database: Path,
) -> None:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        annual = connection.execute(
            "SELECT * FROM shadow_time_metrics WHERE period_type='YEAR' ORDER BY method,horizon,period"
        ).fetchdf().to_dict("records")
        monthly = connection.execute(
            "SELECT * FROM shadow_time_metrics WHERE period_type='MONTH' ORDER BY method,horizon,period"
        ).fetchdf().to_dict("records")
        risk = connection.execute(
            "SELECT * FROM shadow_risk_metrics ORDER BY horizon"
        ).fetchdf().to_dict("records")
    finally:
        connection.close()
    payload = {
        "metadata": asdict(result),
        "composite_metrics": [asdict(item) for item in metrics],
        "annual_metrics": annual,
        "monthly_metrics": monthly,
        "risk_metrics": risk,
        "decision_augmentation": {
            "status": result.baseline_status,
            "legacy_vs_shadow_action": "NOT_AVAILABLE",
            "legacy_vs_shadow_confidence": "NOT_AVAILABLE",
            "pre_veto_detection": "NOT_AVAILABLE",
            "reason": "No PIT-safe historical Legacy Decision/VETO dataset covers the Replay period.",
        },
        "market_regime": {"status": "NOT_AVAILABLE"},
        "formal_weight": 0,
        "alpha_conclusion": "NONE",
        "external_api_calls": 0,
        "llm_calls": 0,
    }
    path.write_text(json.dumps(_stable_value(payload),ensure_ascii=False,indent=2),encoding="utf-8")


def write_report(
    path: Path,
    result: S3CShadowRunResult,
    metrics: list[CompositeEvaluationMetric],
    database: Path,
) -> None:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        annual = connection.execute(
            "SELECT period,method,horizon,rank_ic_mean,q5_q1_spread FROM shadow_time_metrics "
            "WHERE period_type='YEAR' ORDER BY method,horizon,period"
        ).fetchall()
        risk = connection.execute(
            "SELECT horizon,reminder_count,valid_label_count,reminder_mean_return,"
            "non_reminder_mean_return,return_difference FROM shadow_risk_metrics ORDER BY horizon"
        ).fetchall()
    finally:
        connection.close()

    def fmt(value: Any) -> str:
        return "NA" if value is None else f"{value:.6f}" if isinstance(value,float) else str(value)

    lines = [
        "# Sentiment V3 S3C Shadow Report", "",
        "## 1. Dataset and isolation", "",
        f"- Run ID: `{result.run_id}`",
        f"- Dataset: `{result.dataset_id}`",
        f"- Range: 2024-08-01 to 2026-08-13; dates={result.date_count}; packets={result.packet_count}",
        "- Record source: `REPLAY`; external API=0; LLM=0; formal_weight=0.",
        "- DecisionEngine, 60/40, A/B Shadow, Canonical Shadow and FORWARD_SHADOW_V1 are not connected.",
        "", "## 2. Candidate features", "",
    ]
    lines += [f"- `{name}` (direction={FEATURE_DIRECTIONS[name]:+d})" for name in CANDIDATE_FEATURES]
    lines += [
        "", "Directions are frozen from S3B2 and are not optimized in S3C.",
        "", "## 3. Composite definitions", "",
        "- `EQUAL_WEIGHT`: equal mean of daily cross-sectional oriented z-scores; at least 2 features.",
        "- `RANK_AVERAGE`: equal mean of daily oriented percentile ranks; at least 2 features.",
        "- `FEATURE_FAMILY_AVERAGE`: equal average of Dragon-Tiger and Trading-Behavior family rank means; both families required.",
        "- Confidence is data completeness (`observed candidates / 4`), not prediction probability or production confidence.",
        "", "## 4. Coverage", "",
        "| Method | Coverage |", "|---|---:|",
    ]
    lines += [f"| {method} | {coverage:.6%} |" for method,coverage in result.method_coverage.items()]
    lines += [
        "", f"Mean research confidence: {result.mean_research_confidence:.6f}.",
        "", "## 5. Future-return research diagnostics", "",
        "These are replay diagnostics, not an Alpha conclusion or tradable strategy.", "",
        "| Method | Label | Horizon | Samples | RankIC | Q5-Q1 |", "|---|---|---:|---:|---:|---:|",
    ]
    for item in metrics:
        lines.append(
            f"| {item.method} | {item.label_type} | {item.horizon} | {item.sample_count} | "
            f"{fmt(item.rank_ic_mean)} | {fmt(item.q5_q1_spread)} |"
        )
    lines += ["", "## 6. Annual stability (Executable)", "",
              "| Year | Method | Horizon | RankIC | Q5-Q1 |", "|---|---|---:|---:|---:|"]
    lines += [f"| {year} | {method} | {horizon} | {fmt(rank_ic)} | {fmt(spread)} |"
              for year,method,horizon,rank_ic,spread in annual]
    lines += [
        "", "Monthly metrics are persisted in `shadow_time_metrics`; no favorable subperiod is selected.",
        "", "## 7. Risk reminder audit", "",
        f"Risk rule `{S3C_RISK_RULE_VERSION}` requires public Dragon-Tiger outflow, above-median turnover and below-median gap follow-through.",
        f"Total reminder rows: {result.risk_reminder_count}.", "",
        "| Horizon | Reminders | Valid labels | Reminder mean | Others mean | Difference |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [f"| {h} | {count} | {valid} | {fmt(reminder)} | {fmt(other)} | {fmt(diff)} |"
              for h,count,valid,reminder,other,diff in risk]
    lines += [
        "", "## 8. Legacy Decision augmentation", "",
        f"- Status: `{result.baseline_status}`.",
        "- The main database has no PIT-safe historical Legacy Decision/confidence/VETO dataset covering this Replay range.",
        "- Therefore action difference, formal confidence change and pre-VETO hit rate are `NOT_AVAILABLE`; they are not reconstructed after the fact.",
        "- The sidecar API guarantees `shadow_action == legacy_action` whenever a valid Legacy reference is supplied.",
        "", "## 9. Market stage", "",
        "`NOT_AVAILABLE`: S3B2 found no frozen PIT-safe market-regime definition. S3C does not invent one.",
        "", "## 10. Conclusion boundary", "",
        "This run establishes a deterministic research composite and旁路 shadow dataset only. It does not prove Alpha, create BUY/SELL/HOLD, change confidence, or authorize production use.",
    ]
    path.write_text("\n".join(lines)+"\n",encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentiment V3 S3C local Research Shadow")
    parser.add_argument("--replay-db",type=Path,default=default_replay_database())
    parser.add_argument("--output-db",type=Path,default=Path("database/sentiment_v3_s3c.duckdb"))
    parser.add_argument("--results",type=Path,default=Path("Sentiment_V3_S3C_Shadow_Results.json"))
    parser.add_argument("--report",type=Path,default=Path("Sentiment_V3_S3C_Shadow_Report.md"))
    args = parser.parse_args()
    runner = S3CResearchShadowRunner(
        replay_database=args.replay_db,output_database=args.output_db,
    )
    result,metrics = runner.run()
    write_results(args.results,result,metrics,args.output_db)
    write_report(args.report,result,metrics,args.output_db)
    print(json.dumps(asdict(result),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "BaselineAvailability","CANDIDATE_FEATURES","CompositeMethod",
    "DecisionAugmentationShadowResult","FEATURE_DIRECTIONS","FEATURE_FAMILIES",
    "FeatureValueQuality","LegacyDecisionReference","S3CResearchShadowRunner",
    "SentimentResearchPacket","augment_decision","feature_quality",
    "make_research_packet","research_support_index","risk_reminder_from_values",
]
