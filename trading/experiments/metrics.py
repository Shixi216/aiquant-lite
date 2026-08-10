from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import fmean, median, pstdev
from typing import Iterable

from trading.experiments.models import (
    ExperimentRiskFlag,
    LabelStatus,
)
from trading.experiments.schemas import (
    ExperimentObservation,
    ForwardReturnLabel,
    SignalMetricSet,
)


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    left_sum = sum((value - left_mean) ** 2 for value in left)
    right_sum = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    return None if denominator == 0 else numerator / denominator


def deterministic_bootstrap_interval(
    values: Iterable[float],
    *,
    seed: int,
    samples: int = 500,
) -> tuple[float, float] | None:
    population = list(values)
    if len(population) < 2:
        return None
    generator = random.Random(seed)
    means = sorted(
        fmean(generator.choice(population) for _ in population)
        for _ in range(samples)
    )
    return means[int(samples * 0.025)], means[int(samples * 0.975) - 1]


class EvaluationMetricService:
    def calculate(
        self,
        observations: list[ExperimentObservation],
        labels: list[ForwardReturnLabel],
        *,
        minimum_sample_size: int,
        random_seed: int,
    ) -> list[SignalMetricSet]:
        observation_map = {
            item.observation_id: item for item in observations
        }
        grouped: dict[tuple[str, int], list[ForwardReturnLabel]] = defaultdict(list)
        for label in labels:
            observation = observation_map.get(label.observation_id)
            if observation is None:
                continue
            grouped[
                (observation.signal_type.value, label.horizon_trading_days)
            ].append(label)
        output: list[SignalMetricSet] = []
        for (group_name, horizon), group_labels in sorted(grouped.items()):
            valid_pairs = [
                (observation_map[label.observation_id], label)
                for label in group_labels
                if label.label_status == LabelStatus.COMPLETE
                and observation_map[label.observation_id].point_in_time_valid
                and not observation_map[label.observation_id].stale_snapshot
                and label.gross_return is not None
            ]
            returns = [label.gross_return for _, label in valid_pairs]
            excess = [
                label.excess_return
                for _, label in valid_pairs
                if label.excess_return is not None
            ]
            pending_count = sum(
                label.label_status
                in {
                    LabelStatus.PENDING,
                    LabelStatus.INSUFFICIENT_FUTURE_DATA,
                }
                for label in group_labels
            )
            labeled_count = sum(
                label.label_status == LabelStatus.COMPLETE
                for label in group_labels
            )
            flags = [
                ExperimentRiskFlag.RESEARCH_ONLY,
                ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
                ExperimentRiskFlag.MULTIPLE_TESTING_RISK,
                ExperimentRiskFlag.CORPORATE_ACTION_RISK,
            ]
            if len(returns) < minimum_sample_size:
                flags.extend(
                    [
                        ExperimentRiskFlag.SAMPLE_TOO_SMALL,
                        ExperimentRiskFlag.INSUFFICIENT_DATA,
                    ]
                )
            ranks = [float(item.rank) for item, _ in valid_pairs]
            output.append(
                SignalMetricSet(
                    group_name=group_name,
                    horizon_trading_days=horizon,
                    sample_count=len(group_labels),
                    labeled_count=labeled_count,
                    pending_count=pending_count,
                    valid_count=len(returns),
                    win_rate=(
                        None
                        if not returns
                        else sum(value > 0 for value in returns) / len(returns)
                    ),
                    mean_return=None if not returns else fmean(returns),
                    median_return=None if not returns else median(returns),
                    return_std=(
                        None
                        if len(returns) < 2
                        else pstdev(returns)
                    ),
                    positive_excess_rate=(
                        None
                        if not excess
                        else sum(value > 0 for value in excess) / len(excess)
                    ),
                    mean_excess_return=(
                        None if not excess else fmean(excess)
                    ),
                    median_excess_return=(
                        None if not excess else median(excess)
                    ),
                    maximum_favorable_excursion=(
                        max(
                            label.maximum_favorable_excursion
                            for _, label in valid_pairs
                            if label.maximum_favorable_excursion is not None
                        )
                        if any(
                            label.maximum_favorable_excursion is not None
                            for _, label in valid_pairs
                        )
                        else None
                    ),
                    maximum_adverse_excursion=(
                        min(
                            label.maximum_adverse_excursion
                            for _, label in valid_pairs
                            if label.maximum_adverse_excursion is not None
                        )
                        if any(
                            label.maximum_adverse_excursion is not None
                            for _, label in valid_pairs
                        )
                        else None
                    ),
                    rank_return_spearman=_correlation(ranks, returns),
                    top_k_hit_rate=(
                        None
                        if not returns
                        else sum(value > 0 for value in returns) / len(returns)
                    ),
                    confidence_interval=(
                        deterministic_bootstrap_interval(
                            returns,
                            seed=random_seed + horizon,
                        )
                        if len(returns) >= minimum_sample_size
                        else None
                    ),
                    risk_flags=list(dict.fromkeys(flags)),
                )
            )
        return output

    @staticmethod
    def slices(
        observations: list[ExperimentObservation],
        labels: list[ForwardReturnLabel],
        *,
        minimum_sample_size: int,
    ) -> list[dict[str, object]]:
        observation_map = {
            item.observation_id: item for item in observations
        }
        grouped: dict[
            tuple[str, str, int],
            list[ForwardReturnLabel],
        ] = defaultdict(list)
        for label in labels:
            observation = observation_map.get(label.observation_id)
            if observation is None:
                continue
            values: dict[str, list[str]] = {
                "horizon": [str(label.horizon_trading_days)],
                "board": [observation.board or "UNKNOWN"],
                "industry": [observation.industry or "UNKNOWN"],
                "factor_coverage": [observation.factor_coverage],
                "composite_confidence": [
                    (
                        "MISSING"
                        if observation.composite_confidence is None
                        else (
                            "LOW"
                            if observation.composite_confidence < 0.4
                            else (
                                "MEDIUM"
                                if observation.composite_confidence < 0.7
                                else "HIGH"
                            )
                        )
                    )
                ],
                "scanner_score_quantile": [
                    (
                        "MISSING"
                        if observation.scanner_score is None
                        else f"Q{min(4, int(observation.scanner_score * 4) + 1)}"
                    )
                ],
                "anomaly_type": observation.anomaly_types or ["NONE"],
                "freshness": [
                    "STALE" if observation.stale_snapshot else "FRESH"
                ],
                "veto_status": [observation.veto_status],
                "suspension": [
                    (
                        "UNKNOWN"
                        if observation.is_suspended is None
                        else (
                            "SUSPENDED"
                            if observation.is_suspended
                            else "TRADABLE"
                        )
                    )
                ],
                "data_completeness": [
                    "COMPLETE" if observation.data_complete else "PARTIAL"
                ],
                "trade_date_range": [
                    observation.signal_trade_date.strftime("%Y-%m")
                ],
            }
            for dimension, slice_values in values.items():
                for slice_value in slice_values:
                    grouped[
                        (
                            dimension,
                            slice_value,
                            label.horizon_trading_days,
                        )
                    ].append(label)
        result: list[dict[str, object]] = []
        for (dimension, value, horizon), group in sorted(grouped.items()):
            returns = [
                item.gross_return
                for item in group
                if item.label_status == LabelStatus.COMPLETE
                and item.gross_return is not None
                and observation_map[item.observation_id].point_in_time_valid
            ]
            conclusion_available = len(returns) >= minimum_sample_size
            result.append(
                {
                    "dimension": dimension,
                    "slice_value": value,
                    "horizon_trading_days": horizon,
                    "sample_count": len(group),
                    "valid_count": len(returns),
                    "minimum_sample_size": minimum_sample_size,
                    "conclusion_available": conclusion_available,
                    "metrics": (
                        {
                            "mean_return": fmean(returns),
                            "median_return": median(returns),
                            "win_rate": sum(item > 0 for item in returns)
                            / len(returns),
                        }
                        if conclusion_available
                        else {}
                    ),
                    "risk_flags": (
                        [
                            ExperimentRiskFlag.RESEARCH_ONLY.value,
                            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY.value,
                        ]
                        if conclusion_available
                        else [
                            ExperimentRiskFlag.SAMPLE_TOO_SMALL.value,
                            ExperimentRiskFlag.RESEARCH_ONLY.value,
                            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY.value,
                        ]
                    ),
                }
            )
        return result

    @staticmethod
    def data_quality(
        observations: list[ExperimentObservation],
        labels: list[ForwardReturnLabel],
    ) -> dict[str, float | int]:
        observation_count = len(observations)
        label_count = len(labels)
        return {
            "sample_count": observation_count,
            "label_count": label_count,
            "point_in_time_valid_ratio": (
                0.0
                if observation_count == 0
                else sum(item.point_in_time_valid for item in observations)
                / observation_count
            ),
            "stale_signal_ratio": (
                0.0
                if observation_count == 0
                else sum(item.stale_snapshot for item in observations)
                / observation_count
            ),
            "missing_entry_ratio": (
                0.0
                if label_count == 0
                else sum(
                    item.label_status
                    in {LabelStatus.MISSING_ENTRY, LabelStatus.UNTRADABLE}
                    for item in labels
                )
                / label_count
            ),
            "missing_exit_ratio": (
                0.0
                if label_count == 0
                else sum(
                    item.label_status == LabelStatus.MISSING_EXIT
                    for item in labels
                )
                / label_count
            ),
            "untradable_ratio": (
                0.0
                if label_count == 0
                else sum(
                    item.label_status == LabelStatus.UNTRADABLE
                    for item in labels
                )
                / label_count
            ),
            "partial_factor_ratio": (
                0.0
                if observation_count == 0
                else sum(item.factor_coverage != "5/5" for item in observations)
                / observation_count
            ),
            "low_coverage_ratio": (
                0.0
                if observation_count == 0
                else sum(item.factor_coverage in {"0/5", "1/5", "2/5"} for item in observations)
                / observation_count
            ),
            "corporate_action_risk_ratio": (
                0.0
                if label_count == 0
                else sum(
                    ExperimentRiskFlag.CORPORATE_ACTION_RISK in item.risk_flags
                    for item in labels
                )
                / label_count
            ),
        }


__all__ = [
    "EvaluationMetricService",
    "deterministic_bootstrap_interval",
]
