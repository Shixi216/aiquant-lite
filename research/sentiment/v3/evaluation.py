from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import duckdb

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_DATASET_HASH,
    EXPECTED_DATASET_ID,
    EXPECTED_EVALUATION_SHA,
    EXPECTED_LABEL_SPEC_SHA,
    EXPECTED_REGISTRY_SHA,
    MIN_CROSS_SECTION_SIZE,
)
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.research_shadow import (
    S3C_ALGORITHM_VERSION,
)


EVALUATION_ALGORITHM_VERSION = "sentiment-v3-s3c-evaluation-1.0.0"
EVALUATION_SCHEMA_VERSION = "S3C_EVALUATION_DATASET_V1"
REGIME_VERSION = "MARKET_PROXY_REGIME_DIAGNOSTIC_V1"
CONTROL_FACTOR_VERSION = "S3C_CONTROL_FACTORS_V1"
GROUP_ANALYSIS_VERSION = "S3C_DAILY_RANK_GROUPS_V1"
EXPECTED_S3C_RUN_ID = "sv3_s3c_99c3f1bc9a5b7ca408455b45bc7abe45"
EXPECTED_S3C_CONTENT_HASH = (
    "9ed7937ffd5355cf2a95a7a3749e105b554cae0a6f92d5c4d20ef2ab9c0e4f30"
)
DEFAULT_OUTPUT_DIR = Path("evaluation/sentiment_v3_s3c")
METHOD_COLUMNS = {
    "EQUAL_WEIGHT": "equal_weight_score",
    "RANK_AVERAGE": "rank_average_score",
    "FEATURE_FAMILY_AVERAGE": "feature_family_average_score",
}
HORIZONS = (1, 3, 5, 10, 20)
REQUIRED_RETURN_HORIZONS = (1, 5, 10, 20)
CONTROL_FACTORS = (
    "MOMENTUM",
    "VOLATILITY",
    "SIZE",
    "VALUE",
    "QUALITY",
    "LIQUIDITY",
)


class ResearchRegime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    SIDEWAY = "SIDEWAY"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class EvaluationRecommendation(StrEnum):
    REMAIN_RESEARCH_SHADOW = "REMAIN_RESEARCH_SHADOW"
    ELIGIBLE_FOR_SHADOW_DECISION_REVIEW = "ELIGIBLE_FOR_SHADOW_DECISION_REVIEW"


@dataclass(frozen=True, slots=True)
class ICEvaluationSummary:
    score_method: str
    horizon: int
    analysis_scope: str
    analysis_period: str
    valid_dates: int
    sample_count: int
    mean_ic: float | None
    ic_std: float | None
    ic_ir: float | None
    positive_ic_ratio: float | None


@dataclass(frozen=True, slots=True)
class GroupReturnSummary:
    score_method: str
    horizon: int
    group_name: str
    sample_count: int
    valid_dates: int
    mean_forward_return: float | None
    median_forward_return: float | None
    win_rate: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    average_turnover: float | None


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    run_id: str
    deterministic_hash: str
    algorithm_version: str
    source_s3c_run_id: str
    source_s3c_hash: str
    source_dataset_id: str
    source_dataset_hash: str
    date_from: str
    date_to: str
    source_packet_count: int
    evaluation_row_count: int
    date_count: int
    symbol_count: int
    method_count: int
    ic_result_count: int
    group_result_count: int
    available_control_factor_count: int
    unavailable_control_factors: tuple[str, ...]
    regime_source: str
    regime_quality_flag: str
    recommendation: str
    timings: dict[str, float]
    output_database_bytes: int
    record_source: Literal["REPLAY"] = "REPLAY"
    formal_weight: Literal[0] = 0
    external_api_calls: Literal[0] = 0
    llm_calls: Literal[0] = 0
    creates_trade: Literal[False] = False
    alpha_conclusion: Literal["NONE"] = "NONE"


def _stable(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def deterministic_hash(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(_stable(value)).encode("utf-8")
    ).hexdigest()


def calculate_ic_ir(mean_ic: float | None, ic_std: float | None) -> float | None:
    if mean_ic is None or ic_std in (None, 0):
        return None
    return mean_ic / ic_std


def calculate_max_drawdown(daily_returns: list[float]) -> float | None:
    if not daily_returns:
        return None
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in daily_returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    return drawdown


def calculate_membership_turnover(
    current: set[str],
    previous: set[str],
) -> float | None:
    if not current:
        return None
    return 1.0 - len(current & previous) / len(current)


def classify_research_regime(
    *,
    observations: int,
    trend_20d: float | None,
    volatility_20d: float | None,
    prior_volatility_q75: float | None,
    turnover_change_5d: float | None,
) -> ResearchRegime:
    if observations < 20:
        return ResearchRegime.SIDEWAY
    if (
        volatility_20d is not None
        and prior_volatility_q75 is not None
        and volatility_20d > prior_volatility_q75
    ):
        return ResearchRegime.HIGH_VOLATILITY
    if trend_20d is not None and trend_20d > 0 and (turnover_change_5d or 0) >= 0:
        return ResearchRegime.TREND_UP
    if trend_20d is not None and trend_20d < 0 and (turnover_change_5d or 0) <= 0:
        return ResearchRegime.TREND_DOWN
    return ResearchRegime.SIDEWAY


def _write_csv(path: Path, headers: list[str], rows: list[tuple[Any, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


class S3CEvaluationPipeline:
    """Local-only evaluation over the immutable S3C Research Shadow output."""

    def __init__(
        self,
        *,
        s3c_database: Path | str = Path("database/sentiment_v3_s3c.duckdb"),
        replay_database: Path | str = Path("database/sentiment_v3_replay.duckdb"),
        main_database: Path | str = Path("database/hermes_opc.duckdb"),
        output_directory: Path | str = DEFAULT_OUTPUT_DIR,
    ) -> None:
        self.s3c_database = Path(s3c_database)
        self.replay_database = Path(replay_database)
        self.main_database = Path(main_database)
        self.output_directory = Path(output_directory)
        self.output_database = self.output_directory / "s3c_evaluation_v1.duckdb"

    def _load_contract(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.s3c_database), read_only=True)
        try:
            row = connection.execute(
                """
                SELECT run_id,algorithm_version,source_s3b2_run_id,dataset_id,dataset_hash,
                       packet_count,date_count,symbol_count,content_hash,record_source,
                       formal_weight,external_api_calls,llm_calls,creates_trade
                FROM shadow_run_metadata
                """
            ).fetchone()
            if row is None:
                raise ValueError("S3C Research Shadow metadata is unavailable")
            if row[0] != EXPECTED_S3C_RUN_ID or row[8] != EXPECTED_S3C_CONTENT_HASH:
                raise ValueError("S3C Research Shadow run/hash changed")
            if row[1] != S3C_ALGORITHM_VERSION:
                raise ValueError("unexpected S3C algorithm version")
            if row[3] != EXPECTED_DATASET_ID or row[4] != EXPECTED_DATASET_HASH:
                raise ValueError("S3C source Replay dataset changed")
            if row[9:] != ("REPLAY", 0, 0, 0, False):
                raise ValueError("S3C isolation contract changed")
            return {
                "run_id": row[0],
                "dataset_id": row[3],
                "dataset_hash": row[4],
                "packet_count": row[5],
                "date_count": row[6],
                "symbol_count": row[7],
                "content_hash": row[8],
            }
        finally:
            connection.close()

    def _attach_sources(self, connection: duckdb.DuckDBPyConnection) -> None:
        for alias, path in (
            ("s3c", self.s3c_database),
            ("replay", self.replay_database),
            ("main_db", self.main_database),
        ):
            escaped = str(path.resolve()).replace("'", "''")
            connection.execute(f"ATTACH '{escaped}' AS {alias} (READ_ONLY)")

    @staticmethod
    def _validate_pit(connection: duckdb.DuckDBPyConnection) -> None:
        violations = connection.execute(
            """
            SELECT COUNT(*)
            FROM s3c.sentiment_research_packets_wide p
            JOIN replay.replay_feature_packets r ON r.packet_id=p.source_packet_id
            JOIN replay.replay_labels_wide l ON l.packet_id=p.source_packet_id
            WHERE p.record_source <> 'REPLAY'
               OR r.market_available_time > p.cutoff
               OR r.stock_available_time > p.cutoff
               OR r.margin_available_time > p.cutoff
               OR r.dragon_tiger_available_time > p.cutoff
               OR l.exec_1d__entry_date <= p.trade_date
               OR EXTRACT(HOUR FROM p.cutoff) > 9
               OR (EXTRACT(HOUR FROM p.cutoff)=9 AND EXTRACT(MINUTE FROM p.cutoff)>=30)
            """
        ).fetchone()[0]
        if violations:
            raise ValueError(f"S3C Evaluation PIT violations: {violations}")

    @staticmethod
    def _build_regimes(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS regime_diagnostic")
        connection.execute(
            f"""
            CREATE TABLE regime_diagnostic AS
            WITH base AS (
                SELECT trade_date,market_return,market_turnover_change,
                       COUNT(market_return) OVER(
                           ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                       ) observations,
                       SUM(LN(GREATEST(1.0+market_return,0.00000001))) OVER(
                           ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                       ) trend_20d,
                       STDDEV_SAMP(market_return) OVER(
                           ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                       ) volatility_20d,
                       AVG(market_turnover_change) OVER(
                           ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                       ) turnover_change_5d
                FROM replay.replay_market_snapshots
            ), thresholded AS (
                SELECT *,QUANTILE_CONT(volatility_20d,0.75) OVER(
                    ORDER BY trade_date ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
                ) prior_volatility_q75
                FROM base
            )
            SELECT trade_date,market_return AS index_return_proxy,volatility_20d,
                   market_turnover_change,turnover_change_5d,trend_20d,
                   prior_volatility_q75,observations,
                   CASE
                     WHEN observations<20 THEN 'SIDEWAY'
                     WHEN volatility_20d IS NOT NULL AND prior_volatility_q75 IS NOT NULL
                          AND volatility_20d>prior_volatility_q75 THEN 'HIGH_VOLATILITY'
                     WHEN trend_20d>0 AND COALESCE(turnover_change_5d,0)>=0 THEN 'TREND_UP'
                     WHEN trend_20d<0 AND COALESCE(turnover_change_5d,0)<=0 THEN 'TREND_DOWN'
                     ELSE 'SIDEWAY'
                   END market_regime,
                   '{REGIME_VERSION}' regime_version,
                   'MARKET_EQUAL_WEIGHT_RETURN_PROXY' regime_source,
                   'INDEX_HISTORY_UNAVAILABLE' quality_flag,
                   TRUE research_only,FALSE trading_regime
            FROM thresholded
            """
        )

    @staticmethod
    def _build_control_factors(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS control_factors")
        connection.execute(
            f"""
            CREATE TABLE control_factors AS
            WITH adjusted AS (
                SELECT symbol,trade_date,trade_index,close*adj_factor adjusted_close
                FROM replay.replay_price_grid
                WHERE close IS NOT NULL AND adj_factor>0
            ), lagged AS (
                SELECT *,
                    LAG(adjusted_close,1) OVER(PARTITION BY symbol ORDER BY trade_index) prev,
                    LAG(adjusted_close,20) OVER(PARTITION BY symbol ORDER BY trade_index) lag20
                FROM adjusted
            ), returns AS (
                SELECT *,adjusted_close/NULLIF(prev,0)-1.0 daily_return,
                       adjusted_close/NULLIF(lag20,0)-1.0 momentum_20d
                FROM lagged
            ), calculated AS (
                SELECT symbol,trade_date,momentum_20d,
                       STDDEV_SAMP(daily_return) OVER(
                           PARTITION BY symbol ORDER BY trade_index
                           ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                       ) volatility_20d
                FROM returns
            )
            SELECT c.symbol,c.trade_date,c.momentum_20d,c.volatility_20d,
                   CAST(NULL AS DOUBLE) size_factor,
                   CAST(NULL AS DOUBLE) value_factor,
                   CAST(NULL AS DOUBLE) quality_factor,
                   d.turnover_rate liquidity_factor,
                   '{CONTROL_FACTOR_VERSION}' factor_version,
                   'SIZE_VALUE_QUALITY_HISTORY_UNAVAILABLE' quality_flag
            FROM calculated c
            LEFT JOIN replay.replay_daily_basic d USING(symbol,trade_date)
            WHERE c.trade_date BETWEEN DATE '2024-08-01' AND DATE '2026-08-13'
            """
        )

    @staticmethod
    def _build_sector_map(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS packet_sector_map")
        connection.execute(
            """
            CREATE TABLE packet_sector_map AS
            SELECT source_packet_id,industry_name sector,membership_id sector_membership_id
            FROM (
                SELECT p.source_packet_id,h.industry_name,h.membership_id,
                       ROW_NUMBER() OVER(
                           PARTITION BY p.source_packet_id
                           ORDER BY h.valid_from DESC,h.data_available_time DESC,h.membership_id
                       ) selected
                FROM s3c.sentiment_research_packets_wide p
                LEFT JOIN main_db.historical_industry_memberships h
                  ON h.symbol=p.symbol
                 AND p.trade_date>=h.valid_from
                 AND (h.valid_to IS NULL OR p.trade_date<=h.valid_to)
                 AND h.data_available_time<=p.cutoff
            ) WHERE selected=1
            """
        )

    @staticmethod
    def _build_dataset(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS s3c_evaluation_dataset")
        unions = []
        for score_method, column in METHOD_COLUMNS.items():
            unions.append(
                f"SELECT source_packet_id,symbol,trade_date,cutoff,'{score_method}' score_method,"
                f"{column} s3c_score,research_confidence,feature_lineage_hash "
                "FROM s3c.sentiment_research_packets_wide "
                f"WHERE {column} IS NOT NULL"
            )
        long_scores = " UNION ALL ".join(unions)
        connection.execute(
            f"""
            CREATE TABLE s3c_evaluation_dataset AS
            WITH scores AS ({long_scores}), ranked AS (
                SELECT *,CASE WHEN COUNT(*) OVER(PARTITION BY score_method,trade_date)>1 THEN
                    (RANK() OVER(PARTITION BY score_method,trade_date ORDER BY s3c_score)-1.0)/
                    (COUNT(*) OVER(PARTITION BY score_method,trade_date)-1.0) END s3c_rank
                FROM scores
            )
            SELECT s.trade_date AS date,s.symbol AS ticker,s.score_method,s.s3c_score,s.s3c_rank,
                   r.market_regime,
                   CASE WHEN l.exec_1d__status='COMPLETE'
                        THEN l.exec_1d__adjusted_return END forward_return_1d,
                   CASE WHEN l.exec_3d__status='COMPLETE'
                        THEN l.exec_3d__adjusted_return END forward_return_3d,
                   CASE WHEN l.exec_5d__status='COMPLETE'
                        THEN l.exec_5d__adjusted_return END forward_return_5d,
                   CASE WHEN l.exec_10d__status='COMPLETE'
                        THEN l.exec_10d__adjusted_return END forward_return_10d,
                   CASE WHEN l.exec_20d__status='COMPLETE'
                        THEN l.exec_20d__adjusted_return END forward_return_20d,
                   m.sector,c.momentum_20d,c.volatility_20d,c.size_factor,c.value_factor,
                   c.quality_factor,c.liquidity_factor,s.research_confidence,
                   s.source_packet_id,s.feature_lineage_hash,m.sector_membership_id,
                   s.cutoff,'REPLAY' record_source,'{EVALUATION_SCHEMA_VERSION}' schema_version,
                   0 formal_weight,TRUE research_only,FALSE creates_trade
            FROM ranked s
            JOIN replay.replay_labels_wide l ON l.packet_id=s.source_packet_id
            LEFT JOIN regime_diagnostic r ON r.trade_date=s.trade_date
            LEFT JOIN control_factors c ON c.symbol=s.symbol AND c.trade_date=s.trade_date
            LEFT JOIN packet_sector_map m USING(source_packet_id)
            """
        )

    @staticmethod
    def _build_ic(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS daily_rank_ic")
        connection.execute(
            """
            CREATE TABLE daily_rank_ic(
                score_method VARCHAR,trade_date DATE,horizon INTEGER,sample_count BIGINT,
                rank_ic DOUBLE
            )
            """
        )
        for horizon in HORIZONS:
            connection.execute(
                f"""
                INSERT INTO daily_rank_ic
                WITH source AS (
                    SELECT score_method,date,ticker,s3c_rank x,forward_return_{horizon}d y,
                           RANK() OVER(PARTITION BY score_method,date
                                       ORDER BY forward_return_{horizon}d) y_rank
                    FROM s3c_evaluation_dataset
                    WHERE s3c_rank IS NOT NULL AND forward_return_{horizon}d IS NOT NULL
                )
                SELECT score_method,date,{horizon},COUNT(*),CORR(x,y_rank)
                FROM source GROUP BY score_method,date
                """
            )
        connection.execute("DROP TABLE IF EXISTS ic_summary")
        connection.execute(
            f"""
            CREATE TABLE ic_summary AS
            WITH eligible AS (
                SELECT * FROM daily_rank_ic WHERE sample_count>={MIN_CROSS_SECTION_SIZE}
            ), scoped AS (
                SELECT score_method,horizon,'FULL' analysis_scope,'ALL' analysis_period,* EXCLUDE(score_method,horizon)
                FROM eligible
                UNION ALL
                SELECT score_method,horizon,'YEAR',CAST(YEAR(trade_date) AS VARCHAR),
                       * EXCLUDE(score_method,horizon)
                FROM eligible
                UNION ALL
                SELECT score_method,horizon,'QUARTER',
                       CAST(YEAR(trade_date) AS VARCHAR)||'-Q'||
                       CAST(QUARTER(trade_date) AS VARCHAR),* EXCLUDE(score_method,horizon)
                FROM eligible
            )
            SELECT score_method,horizon,analysis_scope,analysis_period,COUNT(*) valid_dates,SUM(sample_count) sample_count,
                   AVG(rank_ic) mean_ic,STDDEV_POP(rank_ic) ic_std,
                   AVG(rank_ic)/NULLIF(STDDEV_POP(rank_ic),0) ic_ir,
                   AVG((rank_ic>0)::INTEGER) positive_ic_ratio
            FROM scoped GROUP BY score_method,horizon,analysis_scope,analysis_period
            """
        )

    @staticmethod
    def _group_case(alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        rank = f"{prefix}s3c_rank"
        return (
            f"SELECT *, 'TOP_10' group_name FROM base WHERE {rank}>=0.90 UNION ALL "
            f"SELECT *, 'TOP_20' FROM base WHERE {rank}>=0.80 UNION ALL "
            f"SELECT *, 'MIDDLE' FROM base WHERE {rank}>0.20 AND {rank}<0.80 UNION ALL "
            f"SELECT *, 'BOTTOM_20' FROM base WHERE {rank}<=0.20 UNION ALL "
            f"SELECT *, 'BOTTOM_10' FROM base WHERE {rank}<=0.10"
        )

    @staticmethod
    def _build_groups(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS rank_group_membership")
        group_sql = S3CEvaluationPipeline._group_case()
        connection.execute(
            f"""
            CREATE TABLE rank_group_membership AS
            WITH base AS (
                SELECT score_method,date,ticker,s3c_rank FROM s3c_evaluation_dataset
                WHERE s3c_rank IS NOT NULL
            ) {group_sql}
            """
        )
        connection.execute("DROP TABLE IF EXISTS daily_group_turnover")
        connection.execute(
            """
            CREATE TABLE daily_group_turnover AS
            WITH indexed AS (
                SELECT *,DENSE_RANK() OVER(PARTITION BY score_method ORDER BY date) date_index
                FROM rank_group_membership
            ), retained AS (
                SELECT cur.score_method,cur.date,cur.group_name,COUNT(*) current_count,
                       COUNT(prev.ticker) retained_count
                FROM indexed cur
                LEFT JOIN indexed prev
                  ON prev.score_method=cur.score_method AND prev.group_name=cur.group_name
                 AND prev.ticker=cur.ticker AND prev.date_index=cur.date_index-1
                GROUP BY cur.score_method,cur.date,cur.group_name
            )
            SELECT *,1.0-retained_count/NULLIF(current_count,0)::DOUBLE turnover
            FROM retained
            """
        )
        connection.execute("DROP TABLE IF EXISTS daily_group_returns")
        connection.execute(
            """
            CREATE TABLE daily_group_returns(
                score_method VARCHAR,trade_date DATE,horizon INTEGER,group_name VARCHAR,
                sample_count BIGINT,mean_return DOUBLE,median_return DOUBLE,win_rate DOUBLE
            )
            """
        )
        for horizon in HORIZONS:
            connection.execute(
                f"""
                INSERT INTO daily_group_returns
                SELECT m.score_method,m.date,{horizon},m.group_name,COUNT(*),
                       AVG(d.forward_return_{horizon}d),MEDIAN(d.forward_return_{horizon}d),
                       AVG((d.forward_return_{horizon}d>0)::INTEGER)
                FROM rank_group_membership m
                JOIN s3c_evaluation_dataset d
                  ON d.score_method=m.score_method AND d.date=m.date AND d.ticker=m.ticker
                WHERE d.forward_return_{horizon}d IS NOT NULL
                GROUP BY m.score_method,m.date,m.group_name
                """
            )
        connection.execute("DROP TABLE IF EXISTS rank_group_summary")
        connection.execute(
            """
            CREATE TABLE rank_group_summary AS
            WITH curve AS (
                SELECT *,EXP(SUM(LN(GREATEST(1.0+mean_return,0.00000001))) OVER(
                           PARTITION BY score_method,horizon,group_name ORDER BY trade_date))-1.0 equity
                FROM daily_group_returns
            ), peak AS (
                SELECT *,MAX(equity) OVER(
                    PARTITION BY score_method,horizon,group_name ORDER BY trade_date
                ) running_peak
                FROM curve
            ), curve_stats AS (
                SELECT score_method,horizon,group_name,SUM(sample_count) sample_count,
                       COUNT(*) valid_dates,AVG(mean_return) mean_forward_return,
                       MEDIAN(median_return) median_forward_return,AVG(win_rate) win_rate,
                       ARG_MAX(equity,trade_date) cumulative_return,
                       MIN((1.0+equity)/NULLIF(1.0+running_peak,0)-1.0) max_drawdown
                FROM peak GROUP BY score_method,horizon,group_name
            ), turnover AS (
                SELECT score_method,group_name,AVG(turnover) average_turnover
                FROM daily_group_turnover GROUP BY score_method,group_name
            )
            SELECT c.*,t.average_turnover
            FROM curve_stats c LEFT JOIN turnover t USING(score_method,group_name)
            """
        )

    @staticmethod
    def _build_factor_correlations(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS factor_correlation_matrix")
        connection.execute(
            """
            CREATE TABLE factor_correlation_matrix(
                score_method VARCHAR,control_factor VARCHAR,available BOOLEAN,
                valid_dates BIGINT,sample_count BIGINT,mean_spearman DOUBLE,
                status VARCHAR,factor_version VARCHAR
            )
            """
        )
        available = {
            "MOMENTUM": "momentum_20d",
            "VOLATILITY": "volatility_20d",
            "LIQUIDITY": "liquidity_factor",
        }
        for score_method in METHOD_COLUMNS:
            for factor in CONTROL_FACTORS:
                column = available.get(factor)
                if column is None:
                    connection.execute(
                        "INSERT INTO factor_correlation_matrix VALUES (?,?,FALSE,0,0,NULL,'NOT_AVAILABLE',?)",
                        [score_method,factor,CONTROL_FACTOR_VERSION],
                    )
                    continue
                row = connection.execute(
                    f"""
                    WITH ranked AS (
                        SELECT date,s3c_rank,
                               RANK() OVER(PARTITION BY date ORDER BY {column}) factor_rank
                        FROM s3c_evaluation_dataset
                        WHERE score_method=? AND s3c_rank IS NOT NULL AND {column} IS NOT NULL
                    ), daily AS (
                        SELECT date,COUNT(*) n,CORR(s3c_rank,factor_rank) correlation
                        FROM ranked GROUP BY date
                    )
                    SELECT COUNT(*) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                           SUM(n) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE}),
                           AVG(correlation) FILTER(WHERE n>={MIN_CROSS_SECTION_SIZE})
                    FROM daily
                    """,
                    [score_method],
                ).fetchone()
                connection.execute(
                    "INSERT INTO factor_correlation_matrix VALUES (?,?,TRUE,?,?,?,'AVAILABLE',?)",
                    [score_method,factor,row[0] or 0,row[1] or 0,row[2],CONTROL_FACTOR_VERSION],
                )

    @staticmethod
    def _build_regime_analysis(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DROP TABLE IF EXISTS regime_ic_summary")
        connection.execute(
            """
            CREATE TABLE regime_ic_summary AS
            SELECT d.score_method,d.horizon,r.market_regime,COUNT(*) valid_dates,
                   SUM(d.sample_count) sample_count,AVG(d.rank_ic) mean_ic,
                   STDDEV_POP(d.rank_ic) ic_std,
                   AVG(d.rank_ic)/NULLIF(STDDEV_POP(d.rank_ic),0) ic_ir,
                   AVG((d.rank_ic>0)::INTEGER) positive_ic_ratio
            FROM daily_rank_ic d JOIN regime_diagnostic r USING(trade_date)
            WHERE d.sample_count>=20
            GROUP BY d.score_method,d.horizon,r.market_regime
            """
        )

    @staticmethod
    def _summary_rows(connection: duckdb.DuckDBPyConnection) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        ic_rows = connection.execute(
            """
            SELECT score_method,horizon,analysis_scope,analysis_period,valid_dates,sample_count,mean_ic,ic_std,
                   ic_ir,positive_ic_ratio
            FROM ic_summary ORDER BY score_method,horizon,analysis_scope,analysis_period
            """
        ).fetchall()
        group_rows = connection.execute(
            """
            SELECT score_method,horizon,group_name,sample_count,valid_dates,
                   mean_forward_return,median_forward_return,win_rate,cumulative_return,
                   max_drawdown,average_turnover
            FROM rank_group_summary ORDER BY score_method,horizon,group_name
            """
        ).fetchall()
        return ic_rows,group_rows

    def _write_outputs(
        self,
        connection: duckdb.DuckDBPyConnection,
        result: EvaluationRunResult,
    ) -> None:
        ic_rows,group_rows = self._summary_rows(connection)
        _write_csv(
            self.output_directory/"ic_summary.csv",
            ["score_method","horizon","analysis_scope","analysis_period","valid_dates","sample_count",
             "mean_ic","ic_std","ic_ir","positive_ic_ratio"],ic_rows,
        )
        _write_csv(
            self.output_directory/"stratified_returns.csv",
            ["score_method","horizon","group","sample_count","valid_dates","mean_return",
             "median_return","win_rate","cumulative_return","max_drawdown","turnover"],
            group_rows,
        )
        correlation_rows = connection.execute(
            "SELECT * FROM factor_correlation_matrix ORDER BY score_method,control_factor"
        ).fetchall()
        _write_csv(
            self.output_directory/"correlation_matrix.csv",
            ["score_method","control_factor","available","valid_dates","sample_count",
             "mean_spearman","status","factor_version"],correlation_rows,
        )
        regime_rows = connection.execute(
            "SELECT market_regime,COUNT(*) FROM regime_diagnostic GROUP BY 1 ORDER BY 1"
        ).fetchall()
        regime_payload = {
            "version":REGIME_VERSION,
            "source":"MARKET_EQUAL_WEIGHT_RETURN_PROXY",
            "quality_flag":"INDEX_HISTORY_UNAVAILABLE",
            "trading_regime":False,
            "pit_method":"trailing windows only; volatility threshold uses prior 60 days",
            "definitions":{
                "HIGH_VOLATILITY":"volatility_20d > prior-60d q75",
                "TREND_UP":"trend_20d > 0 and turnover_change_5d >= 0",
                "TREND_DOWN":"trend_20d < 0 and turnover_change_5d <= 0",
                "SIDEWAY":"otherwise or fewer than 20 observations",
            },
            "distribution":{name:count for name,count in regime_rows},
        }
        (self.output_directory/"regime_diagnostic.json").write_text(
            json.dumps(regime_payload,ensure_ascii=False,indent=2),encoding="utf-8"
        )
        decay_rows = connection.execute(
            """
            SELECT score_method,horizon,mean_ic,ic_std,ic_ir,positive_ic_ratio
            FROM ic_summary WHERE analysis_scope='FULL' ORDER BY score_method,horizon
            """
        ).fetchall()
        decay_payload = {
            "version":EVALUATION_ALGORITHM_VERSION,
            "metric":"daily cross-sectional RankIC",
            "horizons":[1,3,5,10,20],
            "curves":[{
                "score_method":row[0],"horizon":row[1],"mean_ic":row[2],"ic_std":row[3],
                "ic_ir":row[4],"positive_ic_ratio":row[5],
            } for row in decay_rows],
        }
        (self.output_directory/"decay_curve.json").write_text(
            json.dumps(_stable(decay_payload),ensure_ascii=False,indent=2),encoding="utf-8"
        )
        (self.output_directory/"run_metadata.json").write_text(
            json.dumps(_stable(asdict(result)),ensure_ascii=False,indent=2),encoding="utf-8"
        )
        self._write_report(connection,result)

    def _write_report(
        self,
        connection: duckdb.DuckDBPyConnection,
        result: EvaluationRunResult,
    ) -> None:
        full_ic = connection.execute(
            """
            SELECT score_method,horizon,valid_dates,mean_ic,ic_std,ic_ir,positive_ic_ratio
            FROM ic_summary WHERE analysis_scope='FULL' ORDER BY score_method,horizon
            """
        ).fetchall()
        groups = connection.execute(
            """
            SELECT score_method,horizon,group_name,mean_forward_return,median_forward_return,
                   win_rate,cumulative_return,max_drawdown,average_turnover
            FROM rank_group_summary WHERE horizon IN (1,5,10,20)
            ORDER BY score_method,horizon,group_name
            """
        ).fetchall()
        correlations = connection.execute(
            """
            SELECT score_method,control_factor,mean_spearman,status
            FROM factor_correlation_matrix ORDER BY score_method,control_factor
            """
        ).fetchall()
        regimes = connection.execute(
            """
            SELECT score_method,horizon,market_regime,valid_dates,mean_ic,ic_ir
            FROM regime_ic_summary ORDER BY score_method,horizon,market_regime
            """
        ).fetchall()

        def fmt(value: Any) -> str:
            if value is None:
                return "NA"
            if isinstance(value,float):
                return f"{value:.6f}"
            return str(value)

        lines = [
            "# Sentiment V3 S3C Evaluation Report", "",
            "## 1. 数据说明", "",
            f"- Run ID: `{result.run_id}`",
            f"- Deterministic hash: `{result.deterministic_hash}`",
            f"- Source S3C: `{result.source_s3c_run_id}`",
            f"- Range: {result.date_from} ~ {result.date_to}",
            f"- Evaluation rows: {result.evaluation_row_count:,}; symbols: {result.symbol_count:,}; dates: {result.date_count}",
            "- Record source=`REPLAY`; formal_weight=0; external API=0; LLM=0; creates_trade=false.",
            "", "## 2. 方法说明", "",
            "- 三种Score：Equal Weight、Rank Average、Feature Family Average。",
            "- Forward return使用冻结的Executable NEXT_OPEN_TO_CLOSE adjusted labels。",
            "- RankIC按每日横截面计算，再按全期/年度/季度汇总。",
            "- Sector使用历史membership及available_time约束；不使用当前membership。",
            "- 分层累计收益与最大回撤是重叠Forward Label的研究伪曲线，不等于可执行投资组合净值。",
            "", "## 3. IC结果", "",
            "| Method | Horizon | Dates | Mean RankIC | IC Std | ICIR | Positive Ratio |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        lines += [
            f"| {score_method} | {horizon} | {dates} | {fmt(ic)} | {fmt(std)} | {fmt(ir)} | {fmt(pos)} |"
            for score_method,horizon,dates,ic,std,ir,pos in full_ic
        ]
        lines += ["", "完整年度与季度结果见 `ic_summary.csv`。", "", "## 4. 分组收益", "",
                  "| Method | H | Group | Mean | Median | Win | Cumulative* | MDD* | Turnover |",
                  "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        lines += [
            f"| {m} | {h} | {g} | {fmt(mean)} | {fmt(median)} | {fmt(win)} | {fmt(cum)} | {fmt(mdd)} | {fmt(turn)} |"
            for m,h,g,mean,median,win,cum,mdd,turn in groups
        ]
        lines += [
            "", "## 5. 风险分析", "",
            "- Group return存在1/5/10/20日持有期重叠；累计收益和MDD仅用于相对诊断。",
            "- Feature Family Average覆盖集中于龙虎榜事件样本，不能与全市场Score直接解释为同一Universe。",
            "- 不包含交易成本、滑点、容量或真实成交约束的新假设。",
            "", "## 6. 因子相关性", "",
            "| Method | Control | Mean Spearman | Status |", "|---|---|---:|---|",
        ]
        lines += [f"| {m} | {f} | {fmt(corr)} | {status} |" for m,f,corr,status in correlations]
        lines += [
            "", "Size、Value、Quality没有本地PIT历史字段；独立性结论为 `INCONCLUSIVE_INCOMPLETE_CONTROLS`。",
            "", "## 7. Regime分析", "",
            "- 本地无指数历史；使用全市场等权收益代理，质量标记 `INDEX_HISTORY_UNAVAILABLE`。",
            "- Regime仅为研究标签，不是交易状态，不进入DecisionEngine。", "",
            "| Method | H | Regime | Dates | Mean RankIC | ICIR |", "|---|---:|---|---:|---:|---:|",
        ]
        lines += [f"| {m} | {h} | {regime} | {dates} | {fmt(ic)} | {fmt(ir)} |"
                  for m,h,regime,dates,ic,ir in regimes]
        lines += [
            "", "## 8. 下一阶段建议", "",
            f"建议：`{result.recommendation}`。",
            "", "原因：Size/Value/Quality独立性控制缺失、真实指数Regime缺失、且没有覆盖历史期的PIT-safe Legacy Decision基线。继续Research Shadow积累，不进入Shadow Decision。",
            "", "本报告不构成Alpha证明、收益预测、买卖信号或正式策略变更。",
        ]
        (self.output_directory/"Sentiment_V3_S3C_Evaluation_Report.md").write_text(
            "\n".join(lines)+"\n",encoding="utf-8"
        )

    def run(self) -> EvaluationRunResult:
        started = perf_counter()
        contract_started = perf_counter()
        source = self._load_contract()
        contract_seconds = perf_counter()-contract_started
        self.output_directory.mkdir(parents=True,exist_ok=True)
        connection = duckdb.connect(str(self.output_database))
        connection.execute("PRAGMA threads=4")
        try:
            self._attach_sources(connection)
            pit_started = perf_counter()
            self._validate_pit(connection)
            pit_seconds = perf_counter()-pit_started
            regime_started = perf_counter()
            self._build_regimes(connection)
            regime_seconds = perf_counter()-regime_started
            controls_started = perf_counter()
            self._build_control_factors(connection)
            self._build_sector_map(connection)
            controls_seconds = perf_counter()-controls_started
            dataset_started = perf_counter()
            self._build_dataset(connection)
            dataset_seconds = perf_counter()-dataset_started
            ic_started = perf_counter()
            self._build_ic(connection)
            ic_seconds = perf_counter()-ic_started
            group_started = perf_counter()
            self._build_groups(connection)
            group_seconds = perf_counter()-group_started
            correlation_started = perf_counter()
            self._build_factor_correlations(connection)
            correlation_seconds = perf_counter()-correlation_started
            self._build_regime_analysis(connection)
            row = connection.execute(
                """
                SELECT COUNT(*),COUNT(DISTINCT date),COUNT(DISTINCT ticker),
                       MIN(date),MAX(date) FROM s3c_evaluation_dataset
                """
            ).fetchone()
            ic_count = connection.execute("SELECT COUNT(*) FROM ic_summary").fetchone()[0]
            group_count = connection.execute(
                "SELECT COUNT(*) FROM rank_group_summary"
            ).fetchone()[0]
            identity = {
                "algorithm_version":EVALUATION_ALGORITHM_VERSION,
                "source_s3c_hash":source["content_hash"],
                "source_dataset_hash":source["dataset_hash"],
                "registry_hash":EXPECTED_REGISTRY_SHA,
                "label_spec_hash":EXPECTED_LABEL_SPEC_SHA,
                "evaluation_hash":EXPECTED_EVALUATION_SHA,
                "methods":tuple(METHOD_COLUMNS),
                "horizons":HORIZONS,
                "regime_version":REGIME_VERSION,
                "control_factor_version":CONTROL_FACTOR_VERSION,
            }
            run_id = "sv3_s3c_eval_"+deterministic_hash(identity)[:32]
            hash_payload = {
                **identity,"evaluation_rows":row[0],"dates":row[1],"symbols":row[2],
                "date_from":str(row[3]),"date_to":str(row[4]),
                "ic_summary":self._summary_rows(connection)[0],
                "group_summary":self._summary_rows(connection)[1],
            }
            timings = {
                "contract_seconds":contract_seconds,"pit_audit_seconds":pit_seconds,
                "regime_seconds":regime_seconds,"controls_and_sector_seconds":controls_seconds,
                "dataset_seconds":dataset_seconds,"ic_seconds":ic_seconds,
                "group_seconds":group_seconds,"correlation_seconds":correlation_seconds,
                "total_seconds":perf_counter()-started,
            }
            connection.execute("CHECKPOINT")
            database_bytes = self.output_database.stat().st_size
            result = EvaluationRunResult(
                run_id=run_id,deterministic_hash=deterministic_hash(hash_payload),
                algorithm_version=EVALUATION_ALGORITHM_VERSION,
                source_s3c_run_id=source["run_id"],source_s3c_hash=source["content_hash"],
                source_dataset_id=source["dataset_id"],source_dataset_hash=source["dataset_hash"],
                date_from=str(row[3]),date_to=str(row[4]),source_packet_count=source["packet_count"],
                evaluation_row_count=row[0],date_count=row[1],symbol_count=row[2],
                method_count=len(METHOD_COLUMNS),ic_result_count=ic_count,
                group_result_count=group_count,available_control_factor_count=3,
                unavailable_control_factors=("SIZE","VALUE","QUALITY"),
                regime_source="MARKET_EQUAL_WEIGHT_RETURN_PROXY",
                regime_quality_flag="INDEX_HISTORY_UNAVAILABLE",
                recommendation=EvaluationRecommendation.REMAIN_RESEARCH_SHADOW.value,
                timings=timings,output_database_bytes=database_bytes,
            )
            connection.execute("DROP TABLE IF EXISTS evaluation_run_metadata")
            connection.execute(
                """
                CREATE TABLE evaluation_run_metadata AS SELECT
                    ? run_id,? deterministic_hash,? algorithm_version,? source_s3c_run_id,
                    ? source_s3c_hash,? source_dataset_id,? source_dataset_hash,
                    ? evaluation_row_count,? date_count,? symbol_count,? recommendation,
                    'REPLAY' record_source,0 formal_weight,0 external_api_calls,0 llm_calls,
                    FALSE creates_trade,'NONE' alpha_conclusion,NOW() created_at
                """,
                [result.run_id,result.deterministic_hash,result.algorithm_version,
                 result.source_s3c_run_id,result.source_s3c_hash,result.source_dataset_id,
                 result.source_dataset_hash,result.evaluation_row_count,result.date_count,
                 result.symbol_count,result.recommendation],
            )
            connection.execute("CHECKPOINT")
            self._write_outputs(connection,result)
            return result
        finally:
            connection.close()


def main() -> None:
    parser=argparse.ArgumentParser(description="Sentiment V3 S3C Evaluation v1")
    parser.add_argument("--s3c-db",type=Path,default=Path("database/sentiment_v3_s3c.duckdb"))
    parser.add_argument("--replay-db",type=Path,default=Path("database/sentiment_v3_replay.duckdb"))
    parser.add_argument("--main-db",type=Path,default=Path("database/hermes_opc.duckdb"))
    parser.add_argument("--output-dir",type=Path,default=DEFAULT_OUTPUT_DIR)
    args=parser.parse_args()
    result=S3CEvaluationPipeline(
        s3c_database=args.s3c_db,replay_database=args.replay_db,
        main_database=args.main_db,output_directory=args.output_dir,
    ).run()
    print(json.dumps(_stable(asdict(result)),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "CONTROL_FACTORS","DEFAULT_OUTPUT_DIR","EvaluationRecommendation",
    "EvaluationRunResult","GroupReturnSummary","HORIZONS","ICEvaluationSummary",
    "ResearchRegime","S3CEvaluationPipeline","calculate_ic_ir",
    "calculate_max_drawdown","calculate_membership_turnover",
    "classify_research_regime","deterministic_hash",
]
