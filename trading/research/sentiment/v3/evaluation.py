from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from statistics import fmean, median, pstdev
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from trading.research.sentiment.v3.feature_assembly import ResearchFeaturePacket
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.labels import ForwardReturnLabel, LabelSpec
from trading.research.sentiment.v3.schemas import RecordSource, SnapshotModel


EVALUATION_FRAMEWORK_VERSION = "EVALUATION_FRAMEWORK_V1"
EVALUATION_DATASET_VERSION = "SENTIMENT_V3_EVALUATION_DATASET_V1"


class EvaluationQualityFlag(StrEnum):
    SAMPLE_TOO_SMALL = "SAMPLE_TOO_SMALL"
    FEATURE_MISSING = "FEATURE_MISSING"
    LABEL_INVALID = "LABEL_INVALID"
    ZERO_VARIANCE = "ZERO_VARIANCE"
    OVERLAPPING_LABELS = "OVERLAPPING_LABELS"
    PROFIT_FACTOR_DENOMINATOR_ZERO = "PROFIT_FACTOR_DENOMINATOR_ZERO"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class EvaluationConfig(SnapshotModel):
    evaluation_version: str = EVALUATION_FRAMEWORK_VERSION
    min_cross_section_size: int = Field(default=20, ge=3)
    quantile_counts: tuple[Literal[5, 10], ...] = (5, 10)
    top_n_values: tuple[Literal[10, 20], ...] = (10, 20)
    tail_exclusion_percentages: tuple[Literal[1, 5, 10], ...] = (1, 5, 10)
    ic_method: Literal["CROSS_SECTIONAL_PEARSON_BY_DATE"] = (
        "CROSS_SECTIONAL_PEARSON_BY_DATE"
    )
    rank_ic_method: Literal["CROSS_SECTIONAL_SPEARMAN_BY_DATE"] = (
        "CROSS_SECTIONAL_SPEARMAN_BY_DATE"
    )
    profit_factor_definition: Literal["SUM_POSITIVE_OVER_ABS_SUM_NEGATIVE"] = (
        "SUM_POSITIVE_OVER_ABS_SUM_NEGATIVE"
    )
    pooled_all_dates_ic_allowed: Literal[False] = False
    portfolio_max_drawdown_enabled: Literal[False] = False
    regime_provider_configured: Literal[False] = False


class ReturnStats(SnapshotModel):
    sample_count: int
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    profit_factor: float | None
    mae_mean: float | None = None
    mae_median: float | None = None
    mfe_mean: float | None = None
    mfe_median: float | None = None
    quality_flags: list[EvaluationQualityFlag] = Field(default_factory=list)


class QuantileStats(SnapshotModel):
    quantile_count: int
    groups: dict[int, ReturnStats]


class TopNStats(SnapshotModel):
    n: int
    top: ReturnStats
    bottom: ReturnStats


class DailyCorrelation(SnapshotModel):
    trade_date: date
    cutoff: datetime
    sample_count: int
    ic: float | None
    rank_ic: float | None
    quality_flags: list[EvaluationQualityFlag] = Field(default_factory=list)


class FeatureEvaluationResult(SnapshotModel):
    feature_name: str
    label_spec_hash: str
    label_name: str
    date_from: date
    date_to: date
    original_samples: int
    valid_samples: int
    valid_dates: int
    coverage: float | None
    daily_correlations: list[DailyCorrelation]
    ic_mean: float | None
    ic_median: float | None
    ic_std: float | None
    rank_ic_mean: float | None
    rank_ic_median: float | None
    rank_ic_std: float | None
    quantile_stats: dict[int, QuantileStats]
    top_n_stats: dict[int, TopNStats]
    overall_return_stats: ReturnStats
    tail_exclusion_stats: dict[int, ReturnStats]
    overlapping_labels: bool
    quality_flags: list[EvaluationQualityFlag]
    evaluation_version: str = EVALUATION_FRAMEWORK_VERSION
    alpha_conclusion: str = "NONE"
    formal_weight: float = 0.0
    research_only: bool = True


class ResearchEvaluationRow(SnapshotModel):
    packet_id: str
    label_id: str
    symbol: str
    trade_date: date
    cutoff: datetime
    record_source: RecordSource
    features: dict[str, Any]
    label: ForwardReturnLabel

    @model_validator(mode="after")
    def validate_join(self) -> "ResearchEvaluationRow":
        if self.packet_id != self.label.feature_packet_id:
            raise ValueError("label/feature packet ID mismatch")
        if self.symbol != self.label.symbol:
            raise ValueError("label/feature symbol mismatch")
        if self.trade_date != self.label.signal_trade_date:
            raise ValueError("label/feature trade date mismatch")
        if self.cutoff != self.label.feature_cutoff:
            raise ValueError("label/feature cutoff mismatch")
        if self.record_source != self.label.record_source:
            raise ValueError("label/feature record source mismatch")
        return self


class ResearchEvaluationDatasetDefinition(SnapshotModel):
    dataset_id: str
    dataset_version: str = EVALUATION_DATASET_VERSION
    feature_registry_version: str
    label_schema_version: str
    label_algorithm_version: str
    label_spec_hashes: list[str]
    date_from: date
    date_to: date
    record_source: RecordSource
    packet_ids: list[str]
    label_ids: list[str]
    feature_list: list[str]
    original_row_count: int
    valid_label_count: int
    excluded_label_count: int
    exclusion_reasons: dict[str, int]
    symbol_count: int
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contains_alpha_result: Literal[False] = False
    formal_weight: float = 0.0
    research_only: bool = True


class ResearchEvaluationDataset(SnapshotModel):
    definition: ResearchEvaluationDatasetDefinition
    rows: list[ResearchEvaluationRow]


class ResearchEvaluationDatasetBuilder:
    """Exact-key join. Invalid labels remain rows and are counted, never dropped."""

    @staticmethod
    def build(
        *,
        packets: Sequence[ResearchFeaturePacket],
        labels: Sequence[ForwardReturnLabel],
        feature_registry_version: str,
        created_at: datetime,
    ) -> ResearchEvaluationDataset:
        packet_by_id = {item.packet_id: item for item in packets}
        rows: list[ResearchEvaluationRow] = []
        for label in labels:
            packet = packet_by_id.get(label.feature_packet_id)
            if packet is None:
                raise ValueError(f"missing feature packet: {label.feature_packet_id}")
            rows.append(
                ResearchEvaluationRow(
                    packet_id=packet.packet_id,
                    label_id=label.label_id,
                    symbol=packet.symbol,
                    trade_date=packet.trade_date,
                    cutoff=packet.cutoff,
                    record_source=packet.record_source,
                    features=packet.flat_features(),
                    label=label,
                )
            )
        sources = {item.record_source for item in rows}
        if len(sources) > 1:
            raise ValueError("REAL/TEST/SYNTHETIC/REPLAY rows cannot be mixed")
        dates = [item.trade_date for item in rows]
        if not dates:
            raise ValueError("evaluation dataset requires at least one row")
        reasons = Counter(
            item.label.missing_reason.value
            for item in rows
            if item.label.adjusted_return is None and item.label.missing_reason is not None
        )
        valid = sum(item.label.adjusted_return is not None for item in rows)
        definition_payload = {
            "dataset_version": EVALUATION_DATASET_VERSION,
            "feature_registry_version": feature_registry_version,
            "label_schema_version": rows[0].label.label_schema_version,
            "label_algorithm_version": rows[0].label.label_algorithm_version,
            "label_spec_hashes": sorted({item.label.label_spec_hash for item in rows}),
            "date_from": min(dates),
            "date_to": max(dates),
            "record_source": next(iter(sources)).value,
            "packet_ids": sorted({item.packet_id for item in rows}),
            "label_ids": sorted(item.label_id for item in rows),
            "feature_list": sorted({key for item in rows for key in item.features}),
            "original_row_count": len(rows),
            "valid_label_count": valid,
            "excluded_label_count": len(rows) - valid,
            "exclusion_reasons": dict(sorted(reasons.items())),
        }
        digest = hashlib.sha256(
            canonical_json(definition_payload).encode("utf-8")
        ).hexdigest()
        definition = ResearchEvaluationDatasetDefinition(
            dataset_id=f"sv3_eval_dataset_{digest[:32]}_{next(iter(sources)).value.lower()}",
            **definition_payload,
            symbol_count=len({item.symbol for item in rows}),
            created_at=created_at,
            content_hash=digest,
        )
        return ResearchEvaluationDataset(definition=definition, rows=rows)


class RegimeProvider(Protocol):
    """S3C extension point. S3A intentionally defines no market regimes."""

    def get_regime(self, *, trade_date: date, cutoff: datetime) -> str | None: ...


class NullTestProvider(Protocol):
    """Future permutation/random baseline extension point; not run in S3A."""

    def generate(self, values: Sequence[float], *, seed: int) -> Sequence[float]: ...


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denominator = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denominator == 0:
        return None
    return sum(left * right for left, right in zip(dx, dy, strict=True)) / denominator


def _ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    output = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in range(index, end):
            output[ordered[position][0]] = average_rank
        index = end
    return output


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return _pearson(_ranks(xs), _ranks(ys))


def _profit_factor(returns: Sequence[float]) -> tuple[float | None, list[EvaluationQualityFlag]]:
    positive = sum(value for value in returns if value > 0)
    negative = abs(sum(value for value in returns if value < 0))
    if negative == 0:
        return None, [EvaluationQualityFlag.PROFIT_FACTOR_DENOMINATOR_ZERO]
    return positive / negative, []


def _return_stats(
    values: Sequence[tuple[float, float | None, float | None]],
) -> ReturnStats:
    returns = [item[0] for item in values]
    maes = [item[1] for item in values if item[1] is not None]
    mfes = [item[2] for item in values if item[2] is not None]
    factor, flags = _profit_factor(returns)
    return ReturnStats(
        sample_count=len(returns),
        mean_return=None if not returns else fmean(returns),
        median_return=None if not returns else median(returns),
        win_rate=None if not returns else sum(value > 0 for value in returns) / len(returns),
        profit_factor=factor,
        mae_mean=None if not maes else fmean(maes),
        mae_median=None if not maes else median(maes),
        mfe_mean=None if not mfes else fmean(mfes),
        mfe_median=None if not mfes else median(mfes),
        quality_flags=flags,
    )


class ResearchEvaluator:
    """Mathematical framework only; S3A validates with TEST/SYNTHETIC rows."""

    def __init__(self, config: EvaluationConfig | None = None) -> None:
        self.config = config or EvaluationConfig()

    def evaluate_feature(
        self,
        dataset: ResearchEvaluationDataset,
        *,
        feature_name: str,
        label_spec: LabelSpec,
    ) -> FeatureEvaluationResult:
        selected = [
            item for item in dataset.rows
            if item.label.label_spec_hash == label_spec.spec_hash
        ]
        grouped: dict[tuple[date, datetime], list[tuple[float, float, ForwardReturnLabel]]] = defaultdict(list)
        for item in selected:
            feature = _finite(item.features.get(feature_name))
            result = _finite(item.label.adjusted_return)
            if feature is not None and result is not None:
                grouped[(item.trade_date, item.cutoff)].append((feature, result, item.label))
        daily: list[DailyCorrelation] = []
        valid_rows: list[tuple[float, float, ForwardReturnLabel]] = []
        for (day, cutoff), values in sorted(grouped.items()):
            flags: list[EvaluationQualityFlag] = []
            if len(values) < self.config.min_cross_section_size:
                flags.append(EvaluationQualityFlag.SAMPLE_TOO_SMALL)
                ic = rank_ic = None
            else:
                xs = [item[0] for item in values]
                ys = [item[1] for item in values]
                ic = _pearson(xs, ys)
                rank_ic = _spearman(xs, ys)
                if ic is None or rank_ic is None:
                    flags.append(EvaluationQualityFlag.ZERO_VARIANCE)
            daily.append(
                DailyCorrelation(
                    trade_date=day, cutoff=cutoff, sample_count=len(values),
                    ic=ic, rank_ic=rank_ic, quality_flags=flags,
                )
            )
            valid_rows.extend(values)
        return_values = [
            (item[1], item[2].max_adverse_excursion, item[2].max_favorable_excursion)
            for item in valid_rows
        ]
        quantiles: dict[int, QuantileStats] = {}
        for count in self.config.quantile_counts:
            groups: dict[int, list[tuple[float, float | None, float | None]]] = defaultdict(list)
            for values in grouped.values():
                ordered = sorted(values, key=lambda item: (item[0], item[2].symbol))
                for index, (_, result, label) in enumerate(ordered):
                    group = min(count, math.floor(index * count / len(ordered)) + 1)
                    groups[group].append(
                        (result, label.max_adverse_excursion, label.max_favorable_excursion)
                    )
            quantiles[count] = QuantileStats(
                quantile_count=count,
                groups={group: _return_stats(groups.get(group, [])) for group in range(1, count + 1)},
            )
        top_n: dict[int, TopNStats] = {}
        for n in self.config.top_n_values:
            top: list[tuple[float, float | None, float | None]] = []
            bottom: list[tuple[float, float | None, float | None]] = []
            for values in grouped.values():
                ordered = sorted(values, key=lambda item: (item[0], item[2].symbol))
                for _, result, label in ordered[-n:]:
                    top.append((result, label.max_adverse_excursion, label.max_favorable_excursion))
                for _, result, label in ordered[:n]:
                    bottom.append((result, label.max_adverse_excursion, label.max_favorable_excursion))
            top_n[n] = TopNStats(n=n, top=_return_stats(top), bottom=_return_stats(bottom))
        tail: dict[int, ReturnStats] = {}
        ordered_returns = sorted(return_values, key=lambda item: item[0], reverse=True)
        for percentage in self.config.tail_exclusion_percentages:
            remove = math.ceil(len(ordered_returns) * percentage / 100) if ordered_returns else 0
            tail[percentage] = _return_stats(ordered_returns[remove:])
        ic_values = [item.ic for item in daily if item.ic is not None]
        rank_values = [item.rank_ic for item in daily if item.rank_ic is not None]
        flags = [EvaluationQualityFlag.RESEARCH_ONLY]
        if label_spec.horizon > 1:
            flags.append(EvaluationQualityFlag.OVERLAPPING_LABELS)
        return FeatureEvaluationResult(
            feature_name=feature_name,
            label_spec_hash=label_spec.spec_hash,
            label_name=label_spec.label_name,
            date_from=dataset.definition.date_from,
            date_to=dataset.definition.date_to,
            original_samples=len(selected),
            valid_samples=len(valid_rows),
            valid_dates=sum(item.ic is not None for item in daily),
            coverage=None if not selected else len(valid_rows) / len(selected),
            daily_correlations=daily,
            ic_mean=None if not ic_values else fmean(ic_values),
            ic_median=None if not ic_values else median(ic_values),
            ic_std=None if not ic_values else pstdev(ic_values),
            rank_ic_mean=None if not rank_values else fmean(rank_values),
            rank_ic_median=None if not rank_values else median(rank_values),
            rank_ic_std=None if not rank_values else pstdev(rank_values),
            quantile_stats=quantiles,
            top_n_stats=top_n,
            overall_return_stats=_return_stats(return_values),
            tail_exclusion_stats=tail,
            overlapping_labels=label_spec.horizon > 1,
            quality_flags=flags,
        )


__all__ = [
    "DailyCorrelation",
    "EVALUATION_FRAMEWORK_VERSION",
    "EvaluationConfig",
    "EvaluationQualityFlag",
    "FeatureEvaluationResult",
    "NullTestProvider",
    "QuantileStats",
    "RegimeProvider",
    "ResearchEvaluationDataset",
    "ResearchEvaluationDatasetBuilder",
    "ResearchEvaluationDatasetDefinition",
    "ResearchEvaluationRow",
    "ResearchEvaluator",
    "ReturnStats",
    "TopNStats",
]
