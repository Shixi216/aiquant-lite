from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

import duckdb

from trading.research.sentiment.v3.evaluation_runner import (
    EXPECTED_DATASET_HASH,
    EXPECTED_DATASET_ID,
    EXPECTED_EVALUATION_SHA,
    EXPECTED_LABEL_SPEC_SHA,
    EXPECTED_REGISTRY_SHA,
)
from trading.research.sentiment.v3.feature_correlation import (
    calculate_feature_correlations,
)
from trading.research.sentiment.v3.historical_replay import default_replay_database
from trading.research.sentiment.v3.historical_replay_build import feature_column
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.multiple_testing import benjamini_hochberg


S3B1_EVALUATION_ID = "sv3_s3b1_d671555ffc902594a635d7a172d7dae2"
S3B2_ALGORITHM_VERSION = "sentiment-v3-s3b2-refinement-1.0.0"
S3B2_STABILITY_VERSION = "S3B2_TIME_STABILITY_V1"
S3B2_BASKET_VERSION = "EQUAL_WEIGHT_DAILY_PERCENTILE_V1"
S3B2_RESULT_VERSION = "SENTIMENT_V3_S3B2_RESULT_V1"
FROZEN_FEATURE_COUNT = 38
FROZEN_PROMISING_COUNT = 17


@dataclass(frozen=True, slots=True)
class YearMetric:
    year: int
    valid_dates: int
    ic_mean: float | None
    rank_ic_mean: float | None
    q5_q1_spread: float | None


@dataclass(frozen=True, slots=True)
class HorizonStability:
    horizon: int
    status: str
    direction: str
    years: tuple[YearMetric, ...]


@dataclass(frozen=True, slots=True)
class FeatureStability:
    feature_name: str
    status: str
    stable_horizons: tuple[int, ...]
    period_dependent_horizons: tuple[int, ...]
    failed_horizons: tuple[int, ...]
    horizons: tuple[HorizonStability, ...]


@dataclass(frozen=True, slots=True)
class ForwardWindowStability:
    feature_name: str
    short_status: str
    medium_status: str
    long_status: str
    horizon_rank_ic: dict[int, float | None]


@dataclass(frozen=True, slots=True)
class BasketResult:
    basket_name: str
    components: tuple[str, ...]
    horizon: int
    sample_count: int
    valid_dates: int
    ic_mean: float | None
    rank_ic_mean: float | None
    q5_q1_spread: float | None
    comparison_to_components: str
    status: str = "AVAILABLE"


@dataclass(frozen=True, slots=True)
class S3B2RunResult:
    run_id: str
    algorithm_version: str
    source_evaluation_id: str
    dataset_id: str
    dataset_hash: str
    registry_hash: str
    label_spec_hash: str
    evaluation_framework_hash: str
    feature_count: int
    promising_feature_count: int
    multiple_testing_count: int
    fdr_rejected_count: int
    market_regime_status: str
    candidate_features: tuple[str, ...]
    timings: dict[str, float]
    content_hash: str


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(_sanitize(value)).encode("utf-8")).hexdigest()


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sign(value: float | None, *, minimum: float = 0.005) -> int:
    if value is None or abs(value) < minimum:
        return 0
    return 1 if value > 0 else -1


def classify_horizon(years: list[YearMetric]) -> tuple[str, str]:
    rank_signs = [_sign(item.rank_ic_mean) for item in years]
    material = [item for item in rank_signs if item]
    if not material:
        return "FAILED", "NONE"
    direction = "POSITIVE" if sum(material) > 0 else "NEGATIVE"
    if len(set(material)) > 1:
        return "PERIOD_DEPENDENT", "MIXED"
    target = material[0]
    spread_aligned = sum(_sign(item.q5_q1_spread) == target for item in years) >= 2
    rank_aligned = sum(value == target for value in rank_signs) >= 2
    if rank_aligned and spread_aligned:
        return "STABLE", direction
    return "PERIOD_DEPENDENT", direction


def classify_feature_stability(
    feature_name: str,
    horizons: list[HorizonStability],
) -> FeatureStability:
    stable = tuple(item.horizon for item in horizons if item.status == "STABLE")
    dependent = tuple(
        item.horizon for item in horizons if item.status == "PERIOD_DEPENDENT"
    )
    failed = tuple(item.horizon for item in horizons if item.status == "FAILED")
    if len(stable) >= 3:
        status = "STABLE"
    elif not stable and not dependent:
        status = "FAILED"
    else:
        status = "PERIOD_DEPENDENT"
    return FeatureStability(
        feature_name=feature_name,
        status=status,
        stable_horizons=stable,
        period_dependent_horizons=dependent,
        failed_horizons=failed,
        horizons=tuple(horizons),
    )


def classify_window(values: list[float | None]) -> str:
    material = [value for value in values if value is not None]
    if not material or max(abs(value) for value in material) < 0.01:
        return "NOT_EFFECTIVE"
    signs = {_sign(value, minimum=0.0) for value in material if value != 0}
    if len(signs) > 1:
        return "DIRECTION_REVERSAL"
    direction = "POSITIVE" if next(iter(signs), 0) > 0 else "NEGATIVE"
    return f"EFFECTIVE_{direction}"


def equal_weight_score_expression(rank_columns: list[str], minimum: int) -> str:
    count = " + ".join(f"({_quoted(item)} IS NOT NULL)::INTEGER" for item in rank_columns)
    total = " + ".join(f"COALESCE({_quoted(item)},0.0)" for item in rank_columns)
    return f"CASE WHEN ({count}) >= {minimum} THEN ({total}) / ({count}) ELSE NULL END"


class S3B2ResearchRunner:
    def __init__(
        self,
        *,
        replay_database: Path | str | None = None,
        s3b1_database: Path | str = Path("database/sentiment_v3_s3b1.duckdb"),
        research_set: Path | str = Path("Sentiment_V3_S3B_Research_Set_V1.json"),
    ) -> None:
        self.replay_database = Path(replay_database or default_replay_database())
        self.s3b1_database = Path(s3b1_database)
        self.research_set = Path(research_set)

    def _load_contract(self) -> tuple[dict[str, Any], list[str], list[str], list[dict[str, Any]]]:
        research_set = json.loads(self.research_set.read_text(encoding="utf-8"))
        if research_set["dataset_id"] != EXPECTED_DATASET_ID:
            raise ValueError("unexpected replay dataset id")
        if research_set["dataset_content_hash"] != EXPECTED_DATASET_HASH:
            raise ValueError("unexpected replay dataset hash")
        if research_set["registry_sha256"] != EXPECTED_REGISTRY_SHA:
            raise ValueError("Feature Registry V1 hash changed")
        if research_set["label_spec_sha256"] != EXPECTED_LABEL_SPEC_SHA:
            raise ValueError("Label Spec V1 hash changed")
        if research_set["evaluation_sha256"] != EXPECTED_EVALUATION_SHA:
            raise ValueError("Evaluation Framework V1 hash changed")
        features = list(research_set["feature_names"])
        if len(features) != FROZEN_FEATURE_COUNT or len(set(features)) != len(features):
            raise ValueError("S3B2 requires exactly 38 unique frozen features")

        connection = duckdb.connect(str(self.s3b1_database), read_only=True)
        run_row = connection.execute(
            "SELECT metadata_json FROM s3b1_runs WHERE evaluation_id=?",
            [S3B1_EVALUATION_ID],
        ).fetchone()
        if run_row is None:
            raise ValueError("frozen S3B1 evaluation is unavailable")
        metadata = json.loads(run_row[0])
        for key, expected in (
            ("dataset_hash", EXPECTED_DATASET_HASH),
            ("registry_hash", EXPECTED_REGISTRY_SHA),
            ("label_spec_hash", EXPECTED_LABEL_SPEC_SHA),
            ("evaluation_framework_hash", EXPECTED_EVALUATION_SHA),
        ):
            if metadata[key] != expected:
                raise ValueError(f"S3B1 {key} changed")
        rows = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT result_json FROM s3b1_results WHERE evaluation_id=? "
                "ORDER BY feature_name,label_type,horizon",
                [S3B1_EVALUATION_ID],
            ).fetchall()
        ]
        promising = sorted({
            item[0] for item in connection.execute(
                "SELECT feature_name FROM s3b1_results WHERE evaluation_id=? "
                "GROUP BY feature_name HAVING MIN(classification)='PROMISING'",
                [S3B1_EVALUATION_ID],
            ).fetchall()
        })
        connection.close()
        if len(rows) != FROZEN_FEATURE_COUNT * 10:
            raise ValueError("S3B1 result matrix is incomplete")
        if len(promising) != FROZEN_PROMISING_COUNT:
            raise ValueError("S3B1 promising feature set changed")
        self._validate_pit()
        return metadata, features, promising, rows

    def _validate_pit(self) -> None:
        connection = duckdb.connect(str(self.replay_database), read_only=True)
        leakage = connection.execute(
            """
            SELECT COUNT(*) FROM replay_feature_packets
            WHERE market_available_time>decision_cutoff
               OR stock_available_time>decision_cutoff
               OR margin_available_time>decision_cutoff
               OR dragon_tiger_available_time>decision_cutoff
               OR feature_trade_date>=CAST(decision_cutoff AS DATE)
               OR CAST(timezone('Asia/Shanghai',decision_cutoff) AS TIME)>=TIME '09:30:00'
            """
        ).fetchone()[0]
        connection.close()
        if leakage:
            raise ValueError(f"S3B2 PIT validation failed: {leakage} rows")

    def _fdr(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        statistical = [item for item in rows if item["label_type"] == "STATISTICAL"]
        inputs = [
            (f"{item['feature_name']}::{item['horizon']}D", item["p_value"])
            for item in statistical
        ]
        output = {item.test_id: item for item in benjamini_hochberg(inputs)}
        return [asdict(output[test_id]) for test_id, _ in inputs]

    def _time_stability(
        self,
        promising: list[str],
        rows: list[dict[str, Any]],
    ) -> list[FeatureStability]:
        result_lookup = {
            (item["feature_name"], item["horizon"]): item
            for item in rows if item["label_type"] == "STATISTICAL"
        }
        connection = duckdb.connect(str(self.s3b1_database), read_only=True)
        daily = connection.execute(
            """
            SELECT feature_name,horizon,YEAR(trade_date) year_value,
                   COUNT(ic) valid_dates,AVG(ic) ic_mean,AVG(rank_ic) rank_ic_mean
            FROM s3b1_daily_correlations
            WHERE evaluation_id=? AND label_type='STATISTICAL'
            GROUP BY feature_name,horizon,YEAR(trade_date)
            ORDER BY feature_name,horizon,year_value
            """,
            [S3B1_EVALUATION_ID],
        ).fetchall()
        connection.close()
        daily_lookup = {
            (row[0], int(row[1]), int(row[2])): row[3:] for row in daily
        }
        output: list[FeatureStability] = []
        for feature in promising:
            horizons: list[HorizonStability] = []
            for horizon in (1, 3, 5, 10, 20):
                persisted = result_lookup[(feature, horizon)]
                years: list[YearMetric] = []
                for year in (2024, 2025, 2026):
                    valid_dates, ic_mean, rank_ic_mean = daily_lookup.get(
                        (feature, horizon, year), (0, None, None)
                    )
                    time_slice = persisted["time_slices"].get(str(year), {})
                    years.append(YearMetric(
                        year=year,
                        valid_dates=int(valid_dates),
                        ic_mean=ic_mean,
                        rank_ic_mean=rank_ic_mean,
                        q5_q1_spread=time_slice.get("q5_q1_spread"),
                    ))
                status, direction = classify_horizon(years)
                horizons.append(HorizonStability(
                    horizon=horizon,
                    status=status,
                    direction=direction,
                    years=tuple(years),
                ))
            output.append(classify_feature_stability(feature, horizons))
        return output

    @staticmethod
    def _window_stability(
        promising: list[str],
        rows: list[dict[str, Any]],
    ) -> list[ForwardWindowStability]:
        lookup = {
            (item["feature_name"], item["horizon"]): item["rank_ic_mean"]
            for item in rows if item["label_type"] == "STATISTICAL"
        }
        output = []
        for feature in promising:
            values = {horizon: lookup[(feature, horizon)] for horizon in (1, 3, 5, 10, 20)}
            output.append(ForwardWindowStability(
                feature_name=feature,
                short_status=classify_window([values[1], values[3]]),
                medium_status=classify_window([values[5], values[10]]),
                long_status=classify_window([values[20]]),
                horizon_rank_ic=values,
            ))
        return output

    @staticmethod
    def _basket_definitions(promising: list[str]) -> dict[str, list[str]]:
        promising_set = set(promising)
        definitions = {
            "CAPITAL_DRAGON_TIGER_BASKET": [
                item for item in promising if item.startswith("capital.dragon_tiger.")
            ],
            "CAPITAL_MARGIN_BASKET": [
                item for item in promising if item.startswith("capital.margin.")
            ],
            "LIQUIDITY_BASKET": [
                item for item in (
                    "capital.stock.turnover", "stock.relative_market_volume",
                    "stock.turnover_change", "stock.turnover_rate",
                    "stock.volume_change", "stock.volume_ratio",
                ) if item in promising_set
            ],
            "PRICE_REACTION_BASKET": [
                item for item in (
                    "stock.amplitude", "stock.gap_and_follow_through",
                    "stock.intraday_return_pct", "stock.low_to_close_recovery",
                    "stock.open_gap_pct", "stock.relative_market_return", "stock.return_pct",
                ) if item in promising_set
            ],
        }
        return {key: value for key, value in definitions.items() if len(value) >= 2}

    def _basket_results(
        self,
        promising: list[str],
        rows: list[dict[str, Any]],
    ) -> list[BasketResult]:
        definitions = self._basket_definitions(promising)
        connection = duckdb.connect()
        connection.execute("SET threads=1")
        connection.execute("PRAGMA disable_progress_bar")
        database_path = str(self.replay_database.resolve()).replace("'", "''")
        connection.execute(f"ATTACH '{database_path}' AS replay (READ_ONLY)")
        components = sorted({item for values in definitions.values() for item in values})
        rank_expressions: list[str] = []
        rank_names: dict[str, str] = {}
        for index, feature in enumerate(components):
            column = _quoted(feature_column(feature))
            rank_name = f"rank_{index}"
            rank_names[feature] = rank_name
            rank_expressions.append(
                f"CASE WHEN {column} IS NULL OR COUNT({column}) OVER w<=1 THEN NULL "
                f"ELSE (RANK() OVER(PARTITION BY trade_date,decision_cutoff "
                f"ORDER BY {column})-1)::DOUBLE/(COUNT({column}) OVER w-1) END "
                f"AS {_quoted(rank_name)}"
            )
        score_expressions: list[str] = []
        for basket, basket_components in definitions.items():
            rank_columns = [rank_names[item] for item in basket_components]
            minimum = max(2, (len(rank_columns) + 1) // 2)
            score_expressions.append(
                f"{equal_weight_score_expression(rank_columns, minimum)} AS {_quoted(basket)}"
            )
        connection.execute(
            "CREATE TEMP TABLE basket_scores AS WITH ranked AS ("
            "SELECT packet_id,symbol,trade_date,decision_cutoff,"
            + ",".join(rank_expressions)
            + " FROM replay.replay_feature_packets WHERE record_source='REPLAY' "
            "AND CAST(quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%' "
            "WINDOW w AS (PARTITION BY trade_date,decision_cutoff)) "
            "SELECT packet_id,symbol,trade_date,decision_cutoff,"
            + ",".join(score_expressions)
            + " FROM ranked"
        )
        component_lookup = {
            (item["feature_name"], item["horizon"]): item["rank_ic_mean"]
            for item in rows if item["label_type"] == "STATISTICAL"
        }
        output: list[BasketResult] = []
        for basket, basket_components in definitions.items():
            score = _quoted(basket)
            for horizon in (1, 3, 5, 10, 20):
                label = _quoted(f"stat_{horizon}d__adjusted_return")
                row = connection.execute(
                    f"""
                    WITH valid AS (
                        SELECT b.trade_date,b.decision_cutoff,b.symbol,b.{score} x,
                               l.{label}::DOUBLE y
                        FROM basket_scores b
                        JOIN replay.replay_labels_wide l USING(packet_id)
                        WHERE b.{score} IS NOT NULL AND l.{label} IS NOT NULL
                    ), counts AS (
                        SELECT trade_date,decision_cutoff,COUNT(*) n,COUNT(DISTINCT x) unique_x
                        FROM valid GROUP BY trade_date,decision_cutoff
                    ), ranked AS (
                        SELECT v.*,
                               RANK() OVER(PARTITION BY v.trade_date,v.decision_cutoff ORDER BY x) xr,
                               RANK() OVER(PARTITION BY v.trade_date,v.decision_cutoff ORDER BY y) yr,
                               LEAST(5,CEIL(CUME_DIST() OVER(
                                   PARTITION BY v.trade_date,v.decision_cutoff ORDER BY x)*5))::INTEGER q
                        FROM valid v JOIN counts c USING(trade_date,decision_cutoff)
                        WHERE c.n>=20 AND c.unique_x>=5
                    ), daily AS (
                        SELECT trade_date,decision_cutoff,COUNT(*) n,
                               CORR(x,y) ic,CORR(xr,yr) rank_ic
                        FROM ranked GROUP BY trade_date,decision_cutoff
                    ), daily_q AS (
                        SELECT trade_date,decision_cutoff,q,AVG(y) mean_return
                        FROM ranked GROUP BY trade_date,decision_cutoff,q
                    ), spreads AS (
                        SELECT trade_date,decision_cutoff,
                               MAX(mean_return) FILTER(WHERE q=5)-
                               MAX(mean_return) FILTER(WHERE q=1) spread
                        FROM daily_q GROUP BY trade_date,decision_cutoff
                    )
                    SELECT SUM(d.n),COUNT(d.ic),AVG(d.ic),AVG(d.rank_ic),AVG(s.spread)
                    FROM daily d JOIN spreads s USING(trade_date,decision_cutoff)
                    """
                ).fetchone()
                sample_count, valid_dates, ic_mean, rank_ic_mean, spread = row
                component_values = [
                    component_lookup[(item, horizon)]
                    for item in basket_components
                    if component_lookup[(item, horizon)] is not None
                ]
                if rank_ic_mean is None or not component_values:
                    comparison = "NOT_COMPARABLE"
                elif abs(rank_ic_mean) > max(abs(item) for item in component_values):
                    comparison = "ABOVE_BEST_COMPONENT"
                elif abs(rank_ic_mean) > fmean(abs(item) for item in component_values):
                    comparison = "ABOVE_COMPONENT_MEAN"
                else:
                    comparison = "NOT_ABOVE_COMPONENT_MEAN"
                output.append(BasketResult(
                    basket_name=basket,
                    components=tuple(basket_components),
                    horizon=horizon,
                    sample_count=int(sample_count or 0),
                    valid_dates=int(valid_dates or 0),
                    ic_mean=ic_mean,
                    rank_ic_mean=rank_ic_mean,
                    q5_q1_spread=spread,
                    comparison_to_components=comparison,
                ))
        connection.close()
        return output

    def run(self) -> tuple[S3B2RunResult, dict[str, Any]]:
        total_started = perf_counter()
        metadata, features, promising, rows = self._load_contract()
        contract_seconds = perf_counter() - total_started

        started = perf_counter()
        correlations = calculate_feature_correlations(self.replay_database, features)
        correlation_seconds = perf_counter() - started

        started = perf_counter()
        fdr = self._fdr(rows)
        fdr_seconds = perf_counter() - started

        started = perf_counter()
        stability = self._time_stability(promising, rows)
        windows = self._window_stability(promising, rows)
        stability_seconds = perf_counter() - started

        started = perf_counter()
        baskets = self._basket_results(promising, rows)
        basket_seconds = perf_counter() - started

        rejected_by_feature: dict[str, int] = defaultdict(int)
        for item in fdr:
            if item["rejected"]:
                rejected_by_feature[item["test_id"].split("::", 1)[0]] += 1
        stable_features = {
            item.feature_name for item in stability if item.status == "STABLE"
        }
        candidates = tuple(sorted(
            feature for feature in promising
            if feature in stable_features and rejected_by_feature[feature] >= 3
        ))
        timings = {
            "contract_seconds": contract_seconds,
            "correlation_seconds": correlation_seconds,
            "fdr_seconds": fdr_seconds,
            "stability_seconds": stability_seconds,
            "basket_seconds": basket_seconds,
            "total_seconds": perf_counter() - total_started,
        }
        run_payload = {
            "algorithm_version": S3B2_ALGORITHM_VERSION,
            "source_evaluation_id": S3B1_EVALUATION_ID,
            "dataset_hash": metadata["dataset_hash"],
            "registry_hash": metadata["registry_hash"],
            "label_spec_hash": metadata["label_spec_hash"],
            "evaluation_framework_hash": metadata["evaluation_framework_hash"],
            "features": features,
            "promising": promising,
            "candidate_features": candidates,
        }
        run_id = "sv3_s3b2_" + _hash(run_payload)[:32]
        result = S3B2RunResult(
            run_id=run_id,
            algorithm_version=S3B2_ALGORITHM_VERSION,
            source_evaluation_id=S3B1_EVALUATION_ID,
            dataset_id=metadata["dataset_id"],
            dataset_hash=metadata["dataset_hash"],
            registry_hash=metadata["registry_hash"],
            label_spec_hash=metadata["label_spec_hash"],
            evaluation_framework_hash=metadata["evaluation_framework_hash"],
            feature_count=len(features),
            promising_feature_count=len(promising),
            multiple_testing_count=len(fdr),
            fdr_rejected_count=sum(item["rejected"] for item in fdr),
            market_regime_status="NOT_AVAILABLE",
            candidate_features=candidates,
            timings=timings,
            content_hash=_hash({
                "run": run_payload,
                "correlations": [asdict(item) for item in correlations.pairs],
                "families": [asdict(item) for item in correlations.families],
                "fdr": fdr,
                "stability": [asdict(item) for item in stability],
                "windows": [asdict(item) for item in windows],
                "baskets": [asdict(item) for item in baskets],
            }),
        )
        payload = {
            "metadata": asdict(result),
            "correlations": {
                "threshold": 0.8,
                "pairs": [asdict(item) for item in correlations.pairs],
                "highly_correlated_pairs": [
                    asdict(item) for item in correlations.highly_correlated_pairs
                ],
                "families": [asdict(item) for item in correlations.families],
            },
            "fdr": fdr,
            "time_stability": [asdict(item) for item in stability],
            "forward_window_stability": [asdict(item) for item in windows],
            "market_regime": {
                "status": "NOT_AVAILABLE",
                "reason": "No frozen PIT-safe regime definition exists; none was invented.",
            },
            "baskets": [asdict(item) for item in baskets],
            "unavailable_baskets": {
                "MARKET_SENTIMENT_BASKET": "NO_PROMISING_MARKET_COMPONENTS",
                "SPECULATION_BASKET": "NO_PROMISING_SPECULATION_COMPONENTS",
            },
            "formal_weight": 0,
            "alpha_conclusion": "NONE",
            "external_api_calls": 0,
            "llm_calls": 0,
        }
        return result, _sanitize(payload)


def _fmt(value: float | None) -> str:
    return "NA" if value is None or not math.isfinite(value) else f"{value:.6f}"


def build_report(payload: dict[str, Any], output_path: Path | str) -> Path:
    metadata = payload["metadata"]
    high_pairs = payload["correlations"]["highly_correlated_pairs"]
    families = payload["correlations"]["families"]
    fdr = payload["fdr"]
    stability = payload["time_stability"]
    windows = payload["forward_window_stability"]
    baskets = payload["baskets"]
    lines = [
        "# Sentiment V3 S3B2 Research Report", "",
        "## 1. Frozen research input", "",
        f"- Run ID: {metadata['run_id']}",
        f"- Source Evaluation: {metadata['source_evaluation_id']}",
        f"- Dataset: {metadata['dataset_id']}",
        f"- Features: {metadata['feature_count']}",
        f"- S3B1 PROMISING input: {metadata['promising_feature_count']}",
        "- Registry, Label Spec and Evaluation Framework: unchanged", "",
        "## 2. Feature correlation", "",
        "Pearson/Spearman threshold: abs(value) > 0.80. Market pairs use daily observations; "
        "non-market pairs use Packet observations; cross-grain pairs use daily aggregates.", "",
        "| Left | Right | Pearson | Spearman | N | Grain |", "|---|---|---:|---:|---:|---|",
    ]
    for item in high_pairs:
        lines.append(
            f"| {item['left_feature']} | {item['right_feature']} | "
            f"{_fmt(item['pearson'])} | {_fmt(item['spearman'])} | "
            f"{item['sample_count']} | {item['observation_grain']} |"
        )
    if not high_pairs:
        lines.append("| None | None | NA | NA | 0 | - |")
    lines += ["", "No feature was removed; correlation only creates research groups.", "",
              "## 3. Feature groups", ""]
    for family in families:
        lines.append(
            f"- {family['family_id']} [{family['family_type']}]: "
            + ", ".join(family["members"])
        )
    lines += ["", "## 4. BH-FDR", "",
              f"- Declared tests: {metadata['multiple_testing_count']}",
              f"- q <= 0.05: {metadata['fdr_rejected_count']}",
              "- adjusted_p_value and q_value are the same BH-adjusted value; no row was deleted.",
              "- Raw p-values inherit S3B1's documented normal approximation of the daily RankIC mean.", "",
              "| Test | Raw p | Adjusted p | q | Rejected |", "|---|---:|---:|---:|---|"]
    for item in fdr:
        if item["test_id"].split("::", 1)[0] in {
            entry["feature_name"] for entry in stability
        }:
            lines.append(
                f"| {item['test_id']} | {item['raw_p_value']:.6g} | "
                f"{item['adjusted_p_value']:.6g} | {item['q_value']:.6g} | "
                f"{item['rejected']} |"
            )
    lines += ["", "## 5. Time stability", "",
              "| Feature | Status | Stable horizons | Period-dependent | Failed |",
              "|---|---|---|---|---|"]
    for item in stability:
        lines.append(
            f"| {item['feature_name']} | {item['status']} | "
            f"{item['stable_horizons']} | {item['period_dependent_horizons']} | "
            f"{item['failed_horizons']} |"
        )
    lines += ["", "### 5.1 Annual detail", "",
              "| Feature | Year | IC 1/3/5/10/20 | RankIC 1/3/5/10/20 | Q5-Q1 1/3/5/10/20 |",
              "|---|---:|---|---|---|"]
    for item in stability:
        by_year: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
        for horizon in item["horizons"]:
            for year in horizon["years"]:
                by_year[year["year"]][horizon["horizon"]] = year
        for year in (2024, 2025, 2026):
            ic_values = "/".join(
                _fmt(by_year[year][horizon]["ic_mean"]) for horizon in (1, 3, 5, 10, 20)
            )
            rank_values = "/".join(
                _fmt(by_year[year][horizon]["rank_ic_mean"]) for horizon in (1, 3, 5, 10, 20)
            )
            spread_values = "/".join(
                _fmt(by_year[year][horizon]["q5_q1_spread"]) for horizon in (1, 3, 5, 10, 20)
            )
            lines.append(
                f"| {item['feature_name']} | {year} | {ic_values} | "
                f"{rank_values} | {spread_values} |"
            )
    lines += ["", "Annual IC/RankIC were re-aggregated from persisted daily cross-sections; "
              "annual Q5-Q1 uses the immutable S3B1 result lineage.", "",
              "## 6. Forward-window stability", "",
              "| Feature | Short 1/3D | Medium 5/10D | Long 20D | RankIC 1/3/5/10/20 |",
              "|---|---|---|---|---|"]
    for item in windows:
        values = "/".join(_fmt(item["horizon_rank_ic"][h]) for h in (1, 3, 5, 10, 20))
        lines.append(
            f"| {item['feature_name']} | {item['short_status']} | "
            f"{item['medium_status']} | {item['long_status']} | {values} |"
        )
    lines += ["", "## 7. Market regime", "",
              "- Status: NOT_AVAILABLE",
              "- No frozen PIT-safe regime rule exists. Up/down/high-vol/low-vol states were not invented.", "",
              "## 8. Equal-weight research baskets", "",
              "Scores are equal-weight daily cross-sectional percentiles, preserve original feature direction, "
              "and use only S3B1 PROMISING components.", "",
              "| Basket | Components | Horizon | Samples | Dates | IC | RankIC | Q5-Q1 | Comparison |",
              "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for item in baskets:
        lines.append(
            f"| {item['basket_name']} | {', '.join(item['components'])} | "
            f"{item['horizon']}D | {item['sample_count']} | {item['valid_dates']} | "
            f"{_fmt(item['ic_mean'])} | {_fmt(item['rank_ic_mean'])} | "
            f"{_fmt(item['q5_q1_spread'])} | {item['comparison_to_components']} |"
        )
    lines += ["", "Unavailable: MARKET_SENTIMENT_BASKET and SPECULATION_BASKET; "
              "neither has two S3B1 PROMISING components.", "",
              "## 9. Next research candidates", ""]
    candidates = metadata["candidate_features"]
    lines.append(
        ", ".join(candidates) if candidates else
        "None passed the predeclared stability plus >=3 FDR-supported-horizon screen."
    )
    lines += ["", "This is only an S3C research shortlist, not Alpha, ranking, weight, or deployment approval.", "",
              "## 10. Performance and isolation", ""]
    for key, value in metadata["timings"].items():
        lines.append(f"- {key}: {value:.3f}s")
    lines += [
        "- External API calls: 0", "- LLM calls: 0", "- formal_weight: 0",
        "- DecisionEngine, 60/40, A/B Shadow and Canonical Shadow were not used or modified.",
        "- No BUY/SELL/HOLD, order, TradePlan, position change, strategy, forecast, or Alpha conclusion was produced.",
    ]
    path = Path(output_path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sentiment V3 S3B2 local research refinement")
    parser.add_argument("--replay-database", type=Path, default=default_replay_database())
    parser.add_argument("--s3b1-database", type=Path, default=Path("database/sentiment_v3_s3b1.duckdb"))
    parser.add_argument("--research-set", type=Path, default=Path("Sentiment_V3_S3B_Research_Set_V1.json"))
    parser.add_argument("--output-json", type=Path, default=Path("Sentiment_V3_S3B2_Research_Results.json"))
    parser.add_argument("--report", type=Path, default=Path("Sentiment_V3_S3B2_Research_Report.md"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = S3B2ResearchRunner(
        replay_database=args.replay_database,
        s3b1_database=args.s3b1_database,
        research_set=args.research_set,
    )
    result, payload = runner.run()
    args.output_json.write_text(
        json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8"
    )
    build_report(payload,args.report)
    print(json.dumps({
        "metadata": asdict(result),
        "report": str(args.report),
        "results": str(args.output_json),
        "formal_weight": 0,
        "alpha_conclusion": "NONE",
    },ensure_ascii=False,indent=2,default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BasketResult", "FeatureStability", "ForwardWindowStability",
    "HorizonStability", "S3B2ResearchRunner", "S3B2RunResult", "YearMetric",
    "build_report", "classify_feature_stability", "classify_horizon",
    "classify_window", "equal_weight_score_expression", "main",
]
