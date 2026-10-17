from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from time import perf_counter

import duckdb
import pandas as pd

from trading.research.sentiment.v3.historical_replay_build import feature_column


CORRELATION_THRESHOLD = 0.80
MIN_CORRELATION_OBSERVATIONS = 20


@dataclass(frozen=True, slots=True)
class FeatureCorrelationPair:
    left_feature: str
    right_feature: str
    pearson: float | None
    spearman: float | None
    sample_count: int
    observation_grain: str
    highly_correlated: bool


@dataclass(frozen=True, slots=True)
class FeatureFamily:
    family_id: str
    family_type: str
    members: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class CorrelationAnalysis:
    pairs: tuple[FeatureCorrelationPair, ...]
    families: tuple[FeatureFamily, ...]
    elapsed_seconds: float

    @property
    def highly_correlated_pairs(self) -> tuple[FeatureCorrelationPair, ...]:
        return tuple(item for item in self.pairs if item.highly_correlated)


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _correlation_frames(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    numeric = frame.astype("float64")
    pearson = numeric.corr(method="pearson", min_periods=MIN_CORRELATION_OBSERVATIONS)
    ranked = numeric.rank(method="average", na_option="keep")
    spearman = ranked.corr(method="pearson", min_periods=MIN_CORRELATION_OBSERVATIONS)
    valid = numeric.notna().astype("int32")
    counts = valid.T.dot(valid)
    return pearson, spearman, counts


def _value(frame: pd.DataFrame, left: str, right: str) -> float | None:
    value = frame.loc[left, right]
    return None if pd.isna(value) else float(value)


def _semantic_family(feature: str) -> str:
    if feature.startswith("market.") and "turnover" not in feature:
        return "MARKET_BREADTH"
    if feature.startswith("market."):
        return "MARKET_LIQUIDITY"
    if feature.startswith("capital.margin."):
        return "MARGIN_CAPITAL"
    if feature.startswith("capital.dragon_tiger."):
        return "DRAGON_TIGER_EVENT_CAPITAL"
    if feature in {
        "stock.is_limit_up", "stock.is_limit_down", "stock.is_broken_board"
    }:
        return "SPECULATION_STATUS"
    if feature in {
        "capital.stock.turnover", "stock.turnover_change", "stock.turnover_rate",
        "stock.volume_change", "stock.volume_ratio", "stock.relative_market_volume",
    }:
        return "STOCK_LIQUIDITY"
    return "STOCK_PRICE_REACTION"


def _correlation_families(
    features: list[str],
    pairs: list[FeatureCorrelationPair],
) -> list[FeatureFamily]:
    adjacency = {feature: set() for feature in features}
    for pair in pairs:
        if pair.highly_correlated:
            adjacency[pair.left_feature].add(pair.right_feature)
            adjacency[pair.right_feature].add(pair.left_feature)
    output: list[FeatureFamily] = []
    visited: set[str] = set()
    cluster_number = 0
    for feature in features:
        if feature in visited or not adjacency[feature]:
            continue
        stack = [feature]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        visited.update(component)
        cluster_number += 1
        output.append(FeatureFamily(
            family_id=f"CORRELATION_CLUSTER_{cluster_number}",
            family_type="CORRELATION",
            members=tuple(sorted(component)),
            reason=f"abs(Pearson) or abs(Spearman) > {CORRELATION_THRESHOLD:.2f}",
        ))
    semantic: dict[str, list[str]] = {}
    for feature in features:
        semantic.setdefault(_semantic_family(feature), []).append(feature)
    output.extend(
        FeatureFamily(
            family_id=family,
            family_type="SEMANTIC",
            members=tuple(sorted(members)),
            reason="Registry/module semantics; no feature was deleted",
        )
        for family, members in sorted(semantic.items())
    )
    return output


def calculate_feature_correlations(
    replay_database: Path | str,
    features: list[str],
) -> CorrelationAnalysis:
    started = perf_counter()
    market_features = [item for item in features if item.startswith("market.")]
    packet_features = [item for item in features if item not in market_features]
    connection = duckdb.connect(str(replay_database), read_only=True)
    connection.execute("SET threads=1")
    connection.execute("PRAGMA disable_progress_bar")
    where = (
        "record_source='REPLAY' AND "
        "CAST(quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'"
    )

    packet_select = ",".join(
        f"CAST({_quoted(feature_column(item))} AS DOUBLE) AS {_quoted(item)}"
        for item in packet_features
    )
    packet_frame = connection.execute(
        f"SELECT {packet_select} FROM replay_feature_packets WHERE {where} ORDER BY trade_date,symbol"
    ).fetch_df()
    packet_pearson, packet_spearman, packet_counts = _correlation_frames(packet_frame)

    daily_select = ",".join(
        [
            f"ANY_VALUE(CAST({_quoted(feature_column(item))} AS DOUBLE)) AS {_quoted(item)}"
            for item in market_features
        ]
        + [
            f"AVG(CAST({_quoted(feature_column(item))} AS DOUBLE)) AS {_quoted(item)}"
            for item in packet_features
        ]
    )
    daily_frame = connection.execute(
        f"SELECT trade_date,{daily_select} FROM replay_feature_packets "
        f"WHERE {where} GROUP BY trade_date ORDER BY trade_date"
    ).fetch_df().drop(columns=["trade_date"])
    connection.close()
    daily_pearson, daily_spearman, daily_counts = _correlation_frames(daily_frame)

    pairs: list[FeatureCorrelationPair] = []
    for left, right in combinations(features, 2):
        if left in packet_features and right in packet_features:
            pearson_frame, spearman_frame, count_frame = (
                packet_pearson, packet_spearman, packet_counts
            )
            grain = "PACKET"
        else:
            pearson_frame, spearman_frame, count_frame = (
                daily_pearson, daily_spearman, daily_counts
            )
            grain = "DAILY" if left in market_features and right in market_features else "DAILY_AGGREGATE"
        pearson = _value(pearson_frame, left, right)
        spearman = _value(spearman_frame, left, right)
        sample_count = int(count_frame.loc[left, right])
        strength = max(
            abs(pearson) if pearson is not None else 0.0,
            abs(spearman) if spearman is not None else 0.0,
        )
        pairs.append(FeatureCorrelationPair(
            left_feature=left,
            right_feature=right,
            pearson=pearson,
            spearman=spearman,
            sample_count=sample_count,
            observation_grain=grain,
            highly_correlated=strength > CORRELATION_THRESHOLD,
        ))
    del packet_frame, daily_frame
    return CorrelationAnalysis(
        pairs=tuple(pairs),
        families=tuple(_correlation_families(features, pairs)),
        elapsed_seconds=perf_counter() - started,
    )


__all__ = [
    "CORRELATION_THRESHOLD",
    "CorrelationAnalysis",
    "FeatureCorrelationPair",
    "FeatureFamily",
    "calculate_feature_correlations",
]
