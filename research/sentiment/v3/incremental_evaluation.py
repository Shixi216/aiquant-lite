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

from trading.research.sentiment.v3.hashing import canonical_json


ALGORITHM_VERSION = "sentiment-v3-s3c1-incremental-1.0.0"
SCHEMA_VERSION = "S3C1_INCREMENTAL_RESEARCH_V1"
BASELINE_VERSION = "BASELINE_RANK_MODEL_V1"
ORTHOGONAL_VERSION = "SENTIMENT_LIQUIDITY_VOLATILITY_RESIDUAL_V1"
ENVIRONMENT_VERSION = "OBSERVED_MARKET_ENVIRONMENT_V1"
EXPECTED_SOURCE_RUN_ID = "sv3_s3c_eval_13a97d36e922b008cc5437453c9ea8eb"
EXPECTED_SOURCE_HASH = (
    "d63c4693abbd8226b6c132896a973a5ded8a01b3715ff617db707001b8d2c706"
)
DEFAULT_SOURCE_DATABASE = Path(
    "evaluation/sentiment_v3_s3c/s3c_evaluation_v1.duckdb"
)
DEFAULT_OUTPUT_DIRECTORY = Path("evaluation/sentiment_v3_s3c1")
METHODS = ("EQUAL_WEIGHT", "RANK_AVERAGE", "FEATURE_FAMILY_AVERAGE")
HORIZONS = (1, 5, 10, 20)
STABILITY_HORIZONS = (5, 10, 20)
MODEL_TYPES = (
    "BASELINE",
    "SENTIMENT",
    "BASELINE_PLUS_SENTIMENT",
    "SENTIMENT_RESIDUAL",
)
MIN_CROSS_SECTION_SIZE = 20


class ResearchRecommendation(StrEnum):
    CONTINUE_RESEARCH_SHADOW = "CONTINUE_RESEARCH_SHADOW"
    STOP_INCREMENTAL_RESEARCH = "STOP_INCREMENTAL_RESEARCH"


class EnvironmentLabel(StrEnum):
    MARKET_UP = "MARKET_UP"
    MARKET_DOWN = "MARKET_DOWN"
    MARKET_FLAT = "MARKET_FLAT"
    HIGH_TURNOVER = "HIGH_TURNOVER"
    LOW_TURNOVER = "LOW_TURNOVER"
    TURNOVER_UNAVAILABLE = "TURNOVER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class IncrementalRunResult:
    run_id: str
    deterministic_hash: str
    algorithm_version: str
    baseline_version: str
    orthogonal_version: str
    source_run_id: str
    source_hash: str
    date_from: str
    date_to: str
    row_count: int
    date_count: int
    symbol_count: int
    method_count: int
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
    return hashlib.sha256(canonical_json(_stable(value)).encode("utf-8")).hexdigest()


def equal_rank_baseline(
    momentum_rank: float,
    volatility_rank: float,
    liquidity_rank: float,
) -> float:
    """Transparent baseline: momentum + low volatility + liquidity, equal rank weight."""
    return (momentum_rank + (1.0 - volatility_rank) + liquidity_rank) / 3.0


def equal_rank_augmentation(baseline_rank: float, sentiment_rank: float) -> float:
    """Research-only 50/50 rank blend; this is not a formal strategy weight."""
    return (baseline_rank + sentiment_rank) / 2.0


def two_control_residual(
    *,
    sentiment_centered: float,
    liquidity_centered: float,
    volatility_centered: float,
    var_liquidity: float,
    var_volatility: float,
    cov_liquidity_volatility: float,
    cov_sentiment_liquidity: float,
    cov_sentiment_volatility: float,
) -> float | None:
    denominator = (
        var_liquidity * var_volatility
        - cov_liquidity_volatility * cov_liquidity_volatility
    )
    if abs(denominator) <= 1e-15:
        return None
    beta_liquidity = (
        cov_sentiment_liquidity * var_volatility
        - cov_sentiment_volatility * cov_liquidity_volatility
    ) / denominator
    beta_volatility = (
        cov_sentiment_volatility * var_liquidity
        - cov_sentiment_liquidity * cov_liquidity_volatility
    ) / denominator
    return (
        sentiment_centered
        - beta_liquidity * liquidity_centered
        - beta_volatility * volatility_centered
    )


def classify_observed_environments(
    market_return: float | None,
    turnover_change: float | None,
) -> tuple[EnvironmentLabel, EnvironmentLabel]:
    if market_return is None or market_return == 0:
        direction = EnvironmentLabel.MARKET_FLAT
    elif market_return > 0:
        direction = EnvironmentLabel.MARKET_UP
    else:
        direction = EnvironmentLabel.MARKET_DOWN
    if turnover_change is None:
        turnover = EnvironmentLabel.TURNOVER_UNAVAILABLE
    elif turnover_change >= 0:
        turnover = EnvironmentLabel.HIGH_TURNOVER
    else:
        turnover = EnvironmentLabel.LOW_TURNOVER
    return direction, turnover


def _write_csv(path: Path, headers: list[str], rows: list[tuple[Any, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


class S3CIncrementalEvaluationPipeline:
    """Local-only incremental evaluation. It never imports or calls trading paths."""

    def __init__(
        self,
        *,
        source_database: Path | str = DEFAULT_SOURCE_DATABASE,
        output_directory: Path | str = DEFAULT_OUTPUT_DIRECTORY,
    ) -> None:
        self.source_database = Path(source_database)
        self.output_directory = Path(output_directory)
        self.output_database = self.output_directory / "s3c1_incremental_v1.duckdb"

    def _load_contract(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.source_database), read_only=True)
        try:
            row = connection.execute(
                """
                SELECT m.run_id,m.deterministic_hash,m.source_dataset_id,m.source_dataset_hash,
                       m.record_source,m.formal_weight,m.external_api_calls,m.llm_calls,
                       m.creates_trade,MIN(d.date),MAX(d.date),COUNT(*),
                       COUNT(DISTINCT d.date),COUNT(DISTINCT d.ticker),
                       COUNT(DISTINCT d.score_method)
                FROM evaluation_run_metadata m,s3c_evaluation_dataset d
                GROUP BY m.run_id,m.deterministic_hash,m.source_dataset_id,
                         m.source_dataset_hash,m.record_source,m.formal_weight,
                         m.external_api_calls,m.llm_calls,m.creates_trade
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("S3C Evaluation metadata is missing")
            if row[0] != EXPECTED_SOURCE_RUN_ID or row[1] != EXPECTED_SOURCE_HASH:
                raise RuntimeError("S3C Evaluation frozen source contract changed")
            if row[4:9] != ("REPLAY", 0, 0, 0, False):
                raise RuntimeError("S3C Evaluation research isolation contract failed")
            methods = {
                item[0]
                for item in connection.execute(
                    "SELECT DISTINCT score_method FROM s3c_evaluation_dataset"
                ).fetchall()
            }
            if methods != set(METHODS):
                raise RuntimeError(f"Unexpected S3C score methods: {sorted(methods)}")
            return {
                "source_run_id": row[0],
                "source_hash": row[1],
                "source_dataset_id": row[2],
                "source_dataset_hash": row[3],
                "date_from": str(row[9]),
                "date_to": str(row[10]),
                "source_rows": int(row[11]),
                "source_dates": int(row[12]),
                "source_symbols": int(row[13]),
                "source_methods": int(row[14]),
            }
        finally:
            connection.close()

    def _connect_output(self) -> duckdb.DuckDBPyConnection:
        self.output_directory.mkdir(parents=True, exist_ok=True)
        if self.output_database.exists():
            self.output_database.unlink()
        connection = duckdb.connect(str(self.output_database))
        source_path = str(self.source_database.resolve()).replace("'", "''")
        connection.execute(f"ATTACH '{source_path}' AS source_db (READ_ONLY)")
        connection.execute("PRAGMA threads=4")
        return connection

    @staticmethod
    def _build_incremental_dataset(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            f"""
            CREATE TABLE baseline_controls AS
            WITH unique_controls AS (
                SELECT date,ticker,momentum_20d,volatility_20d,liquidity_factor
                FROM source_db.s3c_evaluation_dataset
                WHERE score_method='EQUAL_WEIGHT'
                  AND momentum_20d IS NOT NULL
                  AND volatility_20d IS NOT NULL
                  AND liquidity_factor IS NOT NULL
            ), control_ranks AS (
                SELECT *,
                    PERCENT_RANK() OVER(PARTITION BY date ORDER BY momentum_20d) momentum_rank,
                    PERCENT_RANK() OVER(PARTITION BY date ORDER BY volatility_20d) volatility_rank,
                    PERCENT_RANK() OVER(PARTITION BY date ORDER BY liquidity_factor) liquidity_rank
                FROM unique_controls
            ), baseline AS (
                SELECT *,
                    (momentum_rank+(1.0-volatility_rank)+liquidity_rank)/3.0 baseline_score
                FROM control_ranks
            )
            SELECT *,PERCENT_RANK() OVER(PARTITION BY date ORDER BY baseline_score) baseline_rank,
                   '{BASELINE_VERSION}' baseline_version
            FROM baseline
            """
        )
        connection.execute(
            f"""
            CREATE TABLE incremental_evaluation_dataset AS
            WITH joined AS (
                SELECT d.date,d.ticker,d.score_method,d.s3c_score,d.s3c_rank,
                       b.baseline_score,b.baseline_rank,
                       (b.baseline_rank+d.s3c_rank)/2.0 augmented_score,
                       b.momentum_rank,b.volatility_rank,b.liquidity_rank,
                       d.forward_return_1d,d.forward_return_5d,
                       d.forward_return_10d,d.forward_return_20d,
                       d.market_regime,d.sector,d.source_packet_id,d.feature_lineage_hash,
                       d.cutoff,d.record_source,d.formal_weight,d.research_only,d.creates_trade
                FROM source_db.s3c_evaluation_dataset d
                JOIN baseline_controls b USING(date,ticker)
            ), ranked_augmented AS (
                SELECT *,PERCENT_RANK() OVER(
                    PARTITION BY score_method,date ORDER BY augmented_score
                ) augmented_rank
                FROM joined
            ), centered AS (
                SELECT *,
                    s3c_rank-AVG(s3c_rank) OVER(PARTITION BY score_method,date) sy,
                    liquidity_rank-AVG(liquidity_rank) OVER(PARTITION BY score_method,date) lx,
                    volatility_rank-AVG(volatility_rank) OVER(PARTITION BY score_method,date) vx
                FROM ranked_augmented
            ), moments AS (
                SELECT score_method,date,
                       AVG(lx*lx) var_l,AVG(vx*vx) var_v,AVG(lx*vx) cov_lv,
                       AVG(sy*lx) cov_sl,AVG(sy*vx) cov_sv
                FROM centered GROUP BY score_method,date
            ), residuals AS (
                SELECT c.*,
                    CASE WHEN ABS(m.var_l*m.var_v-m.cov_lv*m.cov_lv)>1e-15 THEN
                        c.sy-
                        ((m.cov_sl*m.var_v-m.cov_sv*m.cov_lv)/
                         (m.var_l*m.var_v-m.cov_lv*m.cov_lv))*c.lx-
                        ((m.cov_sv*m.var_l-m.cov_sl*m.cov_lv)/
                         (m.var_l*m.var_v-m.cov_lv*m.cov_lv))*c.vx
                    END sentiment_residual
                FROM centered c JOIN moments m USING(score_method,date)
            )
            SELECT *,PERCENT_RANK() OVER(
                       PARTITION BY score_method,date ORDER BY sentiment_residual
                   ) residual_rank,
                   '{SCHEMA_VERSION}' schema_version,
                   '{ORTHOGONAL_VERSION}' orthogonal_version
            FROM residuals
            """
        )

    @staticmethod
    def _build_market_environments(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            f"""
            CREATE TABLE market_environments AS
            SELECT trade_date,
                   CASE WHEN index_return_proxy>0 THEN 'MARKET_UP'
                        WHEN index_return_proxy<0 THEN 'MARKET_DOWN'
                        ELSE 'MARKET_FLAT' END direction_environment,
                   CASE WHEN market_turnover_change IS NULL THEN 'TURNOVER_UNAVAILABLE'
                        WHEN market_turnover_change>=0 THEN 'HIGH_TURNOVER'
                        ELSE 'LOW_TURNOVER' END turnover_environment,
                   index_return_proxy,market_turnover_change,
                   '{ENVIRONMENT_VERSION}' environment_version,
                   'OBSERVED_MARKET_SNAPSHOT' environment_source,
                   TRUE research_only,FALSE trading_regime
            FROM source_db.regime_diagnostic
            """
        )

    @staticmethod
    def _model_union(horizon: int) -> str:
        return " UNION ALL ".join(
            (
                f"SELECT score_method,date,ticker,'BASELINE' model_type,baseline_rank model_score,forward_return_{horizon}d future_return FROM incremental_evaluation_dataset",
                f"SELECT score_method,date,ticker,'SENTIMENT',s3c_rank,forward_return_{horizon}d FROM incremental_evaluation_dataset",
                f"SELECT score_method,date,ticker,'BASELINE_PLUS_SENTIMENT',augmented_rank,forward_return_{horizon}d FROM incremental_evaluation_dataset",
                f"SELECT score_method,date,ticker,'SENTIMENT_RESIDUAL',residual_rank,forward_return_{horizon}d FROM incremental_evaluation_dataset",
            )
        )

    @classmethod
    def _build_daily_metrics(cls, connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE daily_model_metrics(
                score_method VARCHAR,model_type VARCHAR,trade_date DATE,horizon INTEGER,
                sample_count BIGINT,pearson_ic DOUBLE,rank_ic DOUBLE,top_bottom_spread DOUBLE
            )
            """
        )
        for horizon in HORIZONS:
            source = cls._model_union(horizon)
            connection.execute(
                f"""
                INSERT INTO daily_model_metrics
                WITH models AS ({source}), valid AS (
                    SELECT *,PERCENT_RANK() OVER(
                        PARTITION BY score_method,model_type,date ORDER BY future_return
                    ) return_rank,
                    PERCENT_RANK() OVER(
                        PARTITION BY score_method,model_type,date ORDER BY model_score
                    ) model_rank
                    FROM models
                    WHERE model_score IS NOT NULL AND future_return IS NOT NULL
                ), daily AS (
                    SELECT score_method,model_type,date,COUNT(*) sample_count,
                           CORR(model_score,future_return) pearson_ic,
                           CORR(model_rank,return_rank) rank_ic,
                           AVG(future_return) FILTER(WHERE model_rank>=0.9)-
                           AVG(future_return) FILTER(WHERE model_rank<=0.1) top_bottom_spread
                    FROM valid GROUP BY score_method,model_type,date
                )
                SELECT score_method,model_type,date,{horizon},sample_count,
                       CASE WHEN sample_count>={MIN_CROSS_SECTION_SIZE} THEN pearson_ic END,
                       CASE WHEN sample_count>={MIN_CROSS_SECTION_SIZE} THEN rank_ic END,
                       CASE WHEN sample_count>={MIN_CROSS_SECTION_SIZE} THEN top_bottom_spread END
                FROM daily
                """
            )
        connection.execute(
            """
            CREATE TABLE model_metric_summary AS
            WITH scopes AS (
                SELECT score_method,model_type,horizon,'FULL' analysis_scope,'ALL' analysis_period,
                       * EXCLUDE(score_method,model_type,horizon)
                FROM daily_model_metrics
                UNION ALL
                SELECT score_method,model_type,horizon,'YEAR',CAST(YEAR(trade_date) AS VARCHAR),
                       * EXCLUDE(score_method,model_type,horizon)
                FROM daily_model_metrics
            )
            SELECT score_method,model_type,horizon,analysis_scope,analysis_period,
                   COUNT(pearson_ic) valid_dates,SUM(sample_count) sample_count,
                   AVG(pearson_ic) mean_pearson_ic,MEDIAN(pearson_ic) median_pearson_ic,
                   STDDEV_SAMP(pearson_ic) pearson_ic_std,
                   AVG(rank_ic) mean_rank_ic,MEDIAN(rank_ic) median_rank_ic,
                   STDDEV_SAMP(rank_ic) rank_ic_std,
                   AVG(rank_ic)/NULLIF(STDDEV_SAMP(rank_ic),0) rank_ic_ir,
                   AVG(CASE WHEN rank_ic>0 THEN 1.0 ELSE 0.0 END) positive_rank_ic_ratio,
                   AVG(top_bottom_spread) mean_top_bottom_spread,
                   MEDIAN(top_bottom_spread) median_top_bottom_spread
            FROM scopes
            GROUP BY score_method,model_type,horizon,analysis_scope,analysis_period
            """
        )

    @staticmethod
    def _build_incremental_comparison(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE incremental_comparison AS
            WITH base AS (
                SELECT * FROM model_metric_summary
                WHERE model_type='BASELINE'
            ), enhanced AS (
                SELECT * FROM model_metric_summary
                WHERE model_type='BASELINE_PLUS_SENTIMENT'
            ), sentiment AS (
                SELECT * FROM model_metric_summary
                WHERE model_type='SENTIMENT'
            )
            SELECT e.score_method,e.horizon,e.analysis_scope,e.analysis_period,
                   e.valid_dates,e.sample_count,
                   b.mean_pearson_ic baseline_pearson_ic,
                   s.mean_pearson_ic sentiment_pearson_ic,
                   e.mean_pearson_ic enhanced_pearson_ic,
                   e.mean_pearson_ic-b.mean_pearson_ic delta_pearson_ic,
                   b.mean_rank_ic baseline_rank_ic,
                   s.mean_rank_ic sentiment_rank_ic,
                   e.mean_rank_ic enhanced_rank_ic,
                   e.mean_rank_ic-b.mean_rank_ic delta_rank_ic,
                   b.mean_top_bottom_spread baseline_top_bottom,
                   s.mean_top_bottom_spread sentiment_top_bottom,
                   e.mean_top_bottom_spread enhanced_top_bottom,
                   e.mean_top_bottom_spread-b.mean_top_bottom_spread delta_top_bottom
            FROM enhanced e
            JOIN base b USING(score_method,horizon,analysis_scope,analysis_period)
            JOIN sentiment s USING(score_method,horizon,analysis_scope,analysis_period)
            """
        )

    @staticmethod
    def _build_environment_summary(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE environment_summary AS
            WITH expanded AS (
                SELECT d.*,e.direction_environment environment_label,'DIRECTION' environment_type
                FROM daily_model_metrics d JOIN market_environments e
                  ON e.trade_date=d.trade_date
                UNION ALL
                SELECT d.*,e.turnover_environment,'TURNOVER'
                FROM daily_model_metrics d JOIN market_environments e
                  ON e.trade_date=d.trade_date
            )
            SELECT score_method,model_type,horizon,environment_type,environment_label,
                   COUNT(rank_ic) valid_dates,SUM(sample_count) sample_count,
                   AVG(pearson_ic) mean_pearson_ic,AVG(rank_ic) mean_rank_ic,
                   STDDEV_SAMP(rank_ic) rank_ic_std,
                   AVG(rank_ic)/NULLIF(STDDEV_SAMP(rank_ic),0) rank_ic_ir,
                   AVG(top_bottom_spread) mean_top_bottom_spread
            FROM expanded
            GROUP BY score_method,model_type,horizon,environment_type,environment_label
            """
        )

    @staticmethod
    def _validate_isolation(connection: duckdb.DuckDBPyConnection) -> None:
        bad = connection.execute(
            """
            SELECT COUNT(*) FROM incremental_evaluation_dataset
            WHERE record_source!='REPLAY' OR formal_weight!=0 OR creates_trade
               OR NOT research_only OR cutoff IS NULL
            """
        ).fetchone()[0]
        lineage_missing = connection.execute(
            """
            SELECT COUNT(*) FROM incremental_evaluation_dataset
            WHERE source_packet_id IS NULL OR feature_lineage_hash IS NULL
            """
        ).fetchone()[0]
        if bad or lineage_missing:
            raise RuntimeError(
                f"S3C1 isolation/lineage failed: bad={bad}, lineage={lineage_missing}"
            )

    @staticmethod
    def _summary_rows(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        incremental = connection.execute(
            """
            SELECT score_method,horizon,baseline_rank_ic,sentiment_rank_ic,
                   enhanced_rank_ic,delta_rank_ic,baseline_top_bottom,
                   enhanced_top_bottom,delta_top_bottom
            FROM incremental_comparison
            WHERE analysis_scope='FULL'
            ORDER BY score_method,horizon
            """
        ).fetchall()
        orthogonal = connection.execute(
            """
            SELECT score_method,horizon,mean_rank_ic,rank_ic_ir,
                   positive_rank_ic_ratio,mean_top_bottom_spread
            FROM model_metric_summary
            WHERE analysis_scope='FULL' AND model_type='SENTIMENT_RESIDUAL'
            ORDER BY score_method,horizon
            """
        ).fetchall()
        stability = connection.execute(
            f"""
            SELECT score_method,model_type,horizon,analysis_period,mean_rank_ic,
                   rank_ic_ir,mean_top_bottom_spread
            FROM model_metric_summary
            WHERE analysis_scope='YEAR' AND horizon IN {STABILITY_HORIZONS}
            ORDER BY score_method,model_type,horizon,analysis_period
            """
        ).fetchall()
        environments = connection.execute(
            """
            SELECT score_method,model_type,horizon,environment_type,environment_label,
                   valid_dates,mean_rank_ic,rank_ic_ir,mean_top_bottom_spread
            FROM environment_summary
            WHERE model_type IN ('SENTIMENT','BASELINE_PLUS_SENTIMENT')
            ORDER BY score_method,model_type,horizon,environment_type,environment_label
            """
        ).fetchall()
        return {
            "incremental": [_stable(row) for row in incremental],
            "orthogonal": [_stable(row) for row in orthogonal],
            "stability": [_stable(row) for row in stability],
            "environments": [_stable(row) for row in environments],
        }

    @staticmethod
    def _recommendation(connection: duckdb.DuckDBPyConnection) -> ResearchRecommendation:
        row = connection.execute(
            """
            SELECT COUNT(*) FILTER(WHERE delta_rank_ic>0),COUNT(*),
                   COUNT(*) FILTER(WHERE residual_rank_ic>0)
            FROM (
                SELECT i.score_method,i.horizon,i.delta_rank_ic,
                       r.mean_rank_ic residual_rank_ic
                FROM incremental_comparison i
                JOIN model_metric_summary r USING(
                    score_method,horizon,analysis_scope,analysis_period
                )
                WHERE i.analysis_scope='FULL'
                  AND i.horizon IN (5,10,20)
                  AND r.model_type='SENTIMENT_RESIDUAL'
            )
            """
        ).fetchone()
        if row[1] and row[0] >= math.ceil(row[1] * 2 / 3) and row[2] >= math.ceil(row[1] * 2 / 3):
            return ResearchRecommendation.CONTINUE_RESEARCH_SHADOW
        return ResearchRecommendation.STOP_INCREMENTAL_RESEARCH

    def _write_outputs(
        self,
        connection: duckdb.DuckDBPyConnection,
        result: IncrementalRunResult,
    ) -> None:
        baseline_rows = connection.execute(
            """
            SELECT score_method,horizon,valid_dates,sample_count,mean_pearson_ic,
                   mean_rank_ic,rank_ic_std,rank_ic_ir,positive_rank_ic_ratio,
                   mean_top_bottom_spread
            FROM model_metric_summary
            WHERE analysis_scope='FULL' AND model_type='BASELINE'
            ORDER BY score_method,horizon
            """
        ).fetchall()
        incremental_rows = connection.execute(
            """
            SELECT score_method,horizon,analysis_scope,analysis_period,valid_dates,
                   sample_count,baseline_pearson_ic,sentiment_pearson_ic,
                   enhanced_pearson_ic,delta_pearson_ic,baseline_rank_ic,
                   sentiment_rank_ic,enhanced_rank_ic,delta_rank_ic,
                   baseline_top_bottom,sentiment_top_bottom,enhanced_top_bottom,
                   delta_top_bottom
            FROM incremental_comparison ORDER BY score_method,horizon,analysis_scope,analysis_period
            """
        ).fetchall()
        orthogonal_rows = connection.execute(
            """
            SELECT score_method,horizon,analysis_scope,analysis_period,valid_dates,
                   sample_count,mean_pearson_ic,mean_rank_ic,rank_ic_std,rank_ic_ir,
                   positive_rank_ic_ratio,mean_top_bottom_spread
            FROM model_metric_summary
            WHERE model_type='SENTIMENT_RESIDUAL'
            ORDER BY score_method,horizon,analysis_scope,analysis_period
            """
        ).fetchall()
        environment_rows = connection.execute(
            "SELECT * FROM environment_summary ORDER BY score_method,model_type,horizon,environment_type,environment_label"
        ).fetchall()
        stability_rows = connection.execute(
            f"""
            SELECT score_method,model_type,horizon,analysis_period,valid_dates,
                   sample_count,mean_pearson_ic,mean_rank_ic,rank_ic_std,rank_ic_ir,
                   positive_rank_ic_ratio,mean_top_bottom_spread
            FROM model_metric_summary
            WHERE analysis_scope='YEAR' AND horizon IN {STABILITY_HORIZONS}
            ORDER BY score_method,model_type,horizon,analysis_period
            """
        ).fetchall()
        _write_csv(
            self.output_directory / "baseline_results.csv",
            ["score_method","horizon","valid_dates","sample_count","mean_pearson_ic",
             "mean_rank_ic","rank_ic_std","rank_ic_ir","positive_rank_ic_ratio",
             "mean_top_bottom_spread"],
            baseline_rows,
        )
        _write_csv(
            self.output_directory / "incremental_results.csv",
            ["score_method","horizon","scope","period","valid_dates","sample_count",
             "baseline_pearson_ic","sentiment_pearson_ic","enhanced_pearson_ic",
             "delta_pearson_ic","baseline_rank_ic","sentiment_rank_ic",
             "enhanced_rank_ic","delta_rank_ic","baseline_top_bottom",
             "sentiment_top_bottom","enhanced_top_bottom","delta_top_bottom"],
            incremental_rows,
        )
        _write_csv(
            self.output_directory / "orthogonal_results.csv",
            ["score_method","horizon","scope","period","valid_dates","sample_count",
             "mean_pearson_ic","mean_rank_ic","rank_ic_std","rank_ic_ir",
             "positive_rank_ic_ratio","mean_top_bottom_spread"],
            orthogonal_rows,
        )
        _write_csv(
            self.output_directory / "environment_results.csv",
            ["score_method","model_type","horizon","environment_type",
             "environment_label","valid_dates","sample_count","mean_pearson_ic",
             "mean_rank_ic","rank_ic_std","rank_ic_ir","mean_top_bottom_spread"],
            environment_rows,
        )
        _write_csv(
            self.output_directory / "time_stability.csv",
            ["score_method","model_type","horizon","year","valid_dates","sample_count",
             "mean_pearson_ic","mean_rank_ic","rank_ic_std","rank_ic_ir",
             "positive_rank_ic_ratio","mean_top_bottom_spread"],
            stability_rows,
        )
        metadata = _stable(asdict(result))
        (self.output_directory / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._write_report(
            result=result,
            incremental_rows=[row for row in incremental_rows if row[2] == "FULL"],
            orthogonal_rows=[row for row in orthogonal_rows if row[2] == "FULL"],
            stability_rows=stability_rows,
            environment_rows=environment_rows,
        )

    def _write_report(
        self,
        *,
        result: IncrementalRunResult,
        incremental_rows: list[tuple[Any, ...]],
        orthogonal_rows: list[tuple[Any, ...]],
        stability_rows: list[tuple[Any, ...]],
        environment_rows: list[tuple[Any, ...]],
    ) -> None:
        def fmt(value: Any) -> str:
            return "NA" if value is None else f"{value:.6f}"

        inc_lines = [
            "| Method | H | Base Pearson | Enhanced Pearson | Delta Pearson | Base RankIC | Sentiment RankIC | Enhanced RankIC | Delta RankIC | Delta Top-Bottom |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in incremental_rows:
            inc_lines.append(
                f"| {row[0]} | {row[1]} | {fmt(row[6])} | {fmt(row[8])} | "
                f"{fmt(row[9])} | {fmt(row[10])} | {fmt(row[11])} | "
                f"{fmt(row[12])} | {fmt(row[13])} | {fmt(row[17])} |"
            )
        residual_lines = [
            "| Method | H | Residual RankIC | ICIR | Positive Ratio | Top-Bottom |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in orthogonal_rows:
            residual_lines.append(
                f"| {row[0]} | {row[1]} | {fmt(row[7])} | {fmt(row[9])} | "
                f"{fmt(row[10])} | {fmt(row[11])} |"
            )
        stability_map = {
            (row[0], row[1], row[2], row[3]): row[7]
            for row in stability_rows
            if row[1] in {"BASELINE_PLUS_SENTIMENT", "SENTIMENT_RESIDUAL"}
        }
        stability_lines = [
            "| Method | Model | H | 2024 RankIC | 2025 RankIC | 2026 RankIC |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for score_method in METHODS:
            for model_type in ("BASELINE_PLUS_SENTIMENT", "SENTIMENT_RESIDUAL"):
                for horizon in STABILITY_HORIZONS:
                    stability_lines.append(
                        f"| {score_method} | {model_type} | {horizon} | "
                        f"{fmt(stability_map.get((score_method, model_type, horizon, '2024')))} | "
                        f"{fmt(stability_map.get((score_method, model_type, horizon, '2025')))} | "
                        f"{fmt(stability_map.get((score_method, model_type, horizon, '2026')))} |"
                    )
        environment_map = {
            (row[0], row[2], row[4]): row[8]
            for row in environment_rows
            if row[1] == "SENTIMENT" and row[2] in STABILITY_HORIZONS
        }
        environment_lines = [
            "| Method | H | Market Up | Market Down | High Turnover | Low Turnover |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for score_method in METHODS:
            for horizon in STABILITY_HORIZONS:
                environment_lines.append(
                    f"| {score_method} | {horizon} | "
                    f"{fmt(environment_map.get((score_method, horizon, 'MARKET_UP')))} | "
                    f"{fmt(environment_map.get((score_method, horizon, 'MARKET_DOWN')))} | "
                    f"{fmt(environment_map.get((score_method, horizon, 'HIGH_TURNOVER')))} | "
                    f"{fmt(environment_map.get((score_method, horizon, 'LOW_TURNOVER')))} |"
                )
        report = f"""# Sentiment V3 S3C1 Incremental Report

## 1. 数据与冻结契约

- Run ID: `{result.run_id}`
- Deterministic hash: `{result.deterministic_hash}`
- Source: `{result.source_run_id}` / `{result.source_hash}`
- Range: {result.date_from} ~ {result.date_to}
- Rows: {result.row_count:,}; dates: {result.date_count}; symbols: {result.symbol_count:,}
- Feature Registry、Label Spec、S3C Evaluation定义均未修改。
- record_source=REPLAY；formal_weight=0；external API=0；LLM=0；creates_trade=false。

## 2. Baseline方法

`BASELINE_RANK_MODEL_V1` 使用每日横截面秩：Momentum正向、Volatility反向、Liquidity正向，三者等权。
增强模型只做研究用等权秩平均：`(baseline_rank + sentiment_rank) / 2`。没有训练、调参或正式权重。

## 3. Incremental结果

{chr(10).join(inc_lines)}

Top-Bottom定义为每日Top 10%减Bottom 10%的平均未来收益差，再跨日平均。

## 4. 去Liquidity与Volatility影响

每日横截面将S3C rank对Liquidity rank和Volatility rank做双变量OLS残差化；不使用未来样本拟合。

{chr(10).join(residual_lines)}

## 5. 时间稳定性

{chr(10).join(stability_lines)}

年份是预先固定切片，没有挑选有利区间。Equal/Rank残差在2024/2025为正、2026转负，不能视为跨期稳定。

## 6. 市场环境

只使用T日已观察到的Market Snapshot：市场收益正/负划分上涨/下跌日，成交额变化正/负划分高/低成交日。
这些是研究标签，不是预测模型或交易Regime。表内为原始Sentiment RankIC。

{chr(10).join(environment_lines)}

## 7. 结论

- Recommendation: `{result.recommendation}`
- Alpha conclusion: `NONE`
- Delta为正主要反映Baseline原始RankIC为负；增强模型本身RankIC较低，不能把Delta解释为可交易改进。
- 本结果只判断增量研究信息，不证明可交易性，不产生BUY/SELL/HOLD。
- 未修改DecisionEngine、60/40、A/B Shadow、Canonical Shadow或任何订单路径。
"""
        (self.output_directory / "Sentiment_V3_S3C1_Incremental_Report.md").write_text(
            report, encoding="utf-8"
        )
    def run(self) -> IncrementalRunResult:
        total_start = perf_counter()
        contract_start = perf_counter()
        contract = self._load_contract()
        contract_seconds = perf_counter() - contract_start
        connection = self._connect_output()
        try:
            dataset_start = perf_counter()
            self._build_incremental_dataset(connection)
            self._build_market_environments(connection)
            self._validate_isolation(connection)
            dataset_seconds = perf_counter() - dataset_start

            metrics_start = perf_counter()
            self._build_daily_metrics(connection)
            self._build_incremental_comparison(connection)
            metrics_seconds = perf_counter() - metrics_start

            environment_start = perf_counter()
            self._build_environment_summary(connection)
            environment_seconds = perf_counter() - environment_start

            row = connection.execute(
                """
                SELECT MIN(date),MAX(date),COUNT(*),COUNT(DISTINCT date),
                       COUNT(DISTINCT ticker),COUNT(DISTINCT score_method)
                FROM incremental_evaluation_dataset
                """
            ).fetchone()
            summaries = self._summary_rows(connection)
            recommendation = self._recommendation(connection).value
            hash_input = {
                "algorithm_version": ALGORITHM_VERSION,
                "baseline_version": BASELINE_VERSION,
                "orthogonal_version": ORTHOGONAL_VERSION,
                "source_run_id": contract["source_run_id"],
                "source_hash": contract["source_hash"],
                "date_from": str(row[0]),
                "date_to": str(row[1]),
                "row_count": int(row[2]),
                "date_count": int(row[3]),
                "symbol_count": int(row[4]),
                "method_count": int(row[5]),
                "summaries": summaries,
                "recommendation": recommendation,
            }
            content_hash = deterministic_hash(hash_input)
            run_id = f"sv3_s3c1_{content_hash[:32]}"
            timings = {
                "contract_seconds": contract_seconds,
                "dataset_seconds": dataset_seconds,
                "metrics_seconds": metrics_seconds,
                "environment_seconds": environment_seconds,
                "total_seconds": perf_counter() - total_start,
            }
            connection.execute(
                """
                CREATE TABLE incremental_run_metadata(
                    run_id VARCHAR,deterministic_hash VARCHAR,algorithm_version VARCHAR,
                    baseline_version VARCHAR,orthogonal_version VARCHAR,source_run_id VARCHAR,
                    source_hash VARCHAR,date_from DATE,date_to DATE,row_count BIGINT,
                    date_count BIGINT,symbol_count BIGINT,method_count BIGINT,
                    recommendation VARCHAR,record_source VARCHAR,formal_weight INTEGER,
                    external_api_calls INTEGER,llm_calls INTEGER,creates_trade BOOLEAN,
                    alpha_conclusion VARCHAR
                )
                """
            )
            connection.execute(
                "INSERT INTO incremental_run_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [run_id,content_hash,ALGORITHM_VERSION,BASELINE_VERSION,ORTHOGONAL_VERSION,
                 contract["source_run_id"],contract["source_hash"],row[0],row[1],row[2],
                 row[3],row[4],row[5],recommendation,"REPLAY",0,0,0,False,"NONE"],
            )
            connection.execute("CHECKPOINT")
            result = IncrementalRunResult(
                run_id=run_id,
                deterministic_hash=content_hash,
                algorithm_version=ALGORITHM_VERSION,
                baseline_version=BASELINE_VERSION,
                orthogonal_version=ORTHOGONAL_VERSION,
                source_run_id=contract["source_run_id"],
                source_hash=contract["source_hash"],
                date_from=str(row[0]),
                date_to=str(row[1]),
                row_count=int(row[2]),
                date_count=int(row[3]),
                symbol_count=int(row[4]),
                method_count=int(row[5]),
                recommendation=recommendation,
                timings=timings,
                output_database_bytes=self.output_database.stat().st_size,
            )
            self._write_outputs(connection, result)
            return result
        finally:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentiment V3 S3C1 incremental research")
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    args = parser.parse_args()
    result = S3CIncrementalEvaluationPipeline(
        source_database=args.source_db,
        output_directory=args.output_dir,
    ).run()
    print(json.dumps(_stable(asdict(result)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "ALGORITHM_VERSION",
    "BASELINE_VERSION",
    "DEFAULT_OUTPUT_DIRECTORY",
    "ENVIRONMENT_VERSION",
    "EnvironmentLabel",
    "HORIZONS",
    "IncrementalRunResult",
    "ORTHOGONAL_VERSION",
    "ResearchRecommendation",
    "S3CIncrementalEvaluationPipeline",
    "classify_observed_environments",
    "deterministic_hash",
    "equal_rank_augmentation",
    "equal_rank_baseline",
    "two_control_residual",
]
