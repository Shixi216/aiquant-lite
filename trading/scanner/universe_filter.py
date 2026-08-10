from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading.scanner.models import FreshnessPolicy, MissingDataPolicy
from trading.scanner.query_ast import (
    BooleanNode,
    BooleanOperator,
    ComparisonNode,
    ComparisonOperator,
    FilterNode,
    MembershipNode,
    MembershipOperator,
    RangeNode,
)
from trading.scanner.schemas import ScannerQueryPlan


@dataclass(frozen=True)
class FilterResult:
    frame: pd.DataFrame
    missing_data_summary: dict[str, int]


@dataclass(frozen=True)
class FilterLayers:
    """Research-only partition; ``core`` is the unchanged formal filter result."""

    core: pd.DataFrame
    near: pd.DataFrame
    control: pd.DataFrame
    missing_data_summary: dict[str, int]


def _node_mask(
    frame: pd.DataFrame,
    node: FilterNode,
) -> tuple[pd.Series, pd.Series]:
    if isinstance(node, BooleanNode):
        results = [_node_mask(frame, child) for child in node.children]
        if node.operator == BooleanOperator.AND:
            matched = results[0][0].copy()
            for child_match, _ in results[1:]:
                matched &= child_match
            missing = results[0][1].copy()
            for _, child_missing in results[1:]:
                missing |= child_missing
            return matched, missing
        matched = results[0][0].copy()
        for child_match, _ in results[1:]:
            matched |= child_match
        any_missing = results[0][1].copy()
        for _, child_missing in results[1:]:
            any_missing |= child_missing
        return matched, any_missing & ~matched

    column = node.field.value
    if column not in frame.columns:
        raise ValueError(f"unsupported scanner field: {column}")
    values = frame[column]
    missing = values.isna()
    if isinstance(node, ComparisonNode):
        operations = {
            ComparisonOperator.EQ: values.eq,
            ComparisonOperator.NE: values.ne,
            ComparisonOperator.GT: values.gt,
            ComparisonOperator.GTE: values.ge,
            ComparisonOperator.LT: values.lt,
            ComparisonOperator.LTE: values.le,
        }
        matched = operations[node.operator](node.value)
    elif isinstance(node, RangeNode):
        lower = (
            values.ge(node.minimum)
            if node.include_minimum
            else values.gt(node.minimum)
        )
        upper = (
            values.le(node.maximum)
            if node.include_maximum
            else values.lt(node.maximum)
        )
        matched = lower & upper
    elif isinstance(node, MembershipNode):
        if node.operator == MembershipOperator.IN:
            matched = values.isin(node.values)
        elif node.operator == MembershipOperator.NOT_IN:
            matched = ~values.isin(node.values)
        elif node.operator == MembershipOperator.CONTAINS:
            matched = values.map(
                lambda value: (
                    False
                    if value is None or not hasattr(value, "__iter__")
                    else any(item in value for item in node.values)
                )
            )
        else:
            matched = values.map(
                lambda value: (
                    True
                    if value is None or not hasattr(value, "__iter__")
                    else all(item not in value for item in node.values)
                )
            )
    else:
        raise TypeError(f"unsupported filter node: {type(node).__name__}")
    return matched.fillna(False), missing


def _near_node_mask(frame: pd.DataFrame, node: FilterNode) -> pd.Series:
    """Return rows just outside a condition without changing its match mask."""

    if isinstance(node, BooleanNode):
        strict = [_node_mask(frame, child)[0] for child in node.children]
        near = [_near_node_mask(frame, child) for child in node.children]
        if node.operator == BooleanOperator.AND:
            result = pd.Series(False, index=frame.index)
            for index in range(len(node.children)):
                candidate = near[index].copy()
                for other_index, other in enumerate(strict):
                    if other_index != index:
                        candidate &= other
                result |= candidate
            return result
        return pd.concat(near, axis=1).any(axis=1) & ~pd.concat(
            strict, axis=1
        ).any(axis=1)

    column = node.field.value
    values = frame[column]
    missing = values.isna()
    matched, _ = _node_mask(frame, node)
    result = pd.Series(False, index=frame.index)
    if isinstance(node, ComparisonNode) and node.operator in {
        ComparisonOperator.GT,
        ComparisonOperator.GTE,
        ComparisonOperator.LT,
        ComparisonOperator.LTE,
    }:
        numeric = pd.to_numeric(values, errors="coerce")
        threshold = float(node.value)
        tolerance = max(abs(threshold), 1.0) * 0.10
        result = numeric.sub(threshold).abs().le(tolerance) & ~matched
    elif isinstance(node, RangeNode):
        numeric = pd.to_numeric(values, errors="coerce")
        width = max(abs(float(node.maximum) - float(node.minimum)), 1.0)
        tolerance = width * 0.10
        distance = pd.concat(
            [
                numeric.sub(float(node.minimum)).abs(),
                numeric.sub(float(node.maximum)).abs(),
            ],
            axis=1,
        ).min(axis=1)
        result = distance.le(tolerance) & ~matched
    return result.fillna(False) & ~missing


def _universe_scope(frame: pd.DataFrame, plan: ScannerQueryPlan) -> pd.DataFrame:
    selected = frame.copy()
    if plan.include_boards:
        selected = selected[selected["board"].isin(plan.include_boards)]
    if plan.exclude_boards:
        selected = selected[~selected["board"].isin(plan.exclude_boards)]
    if plan.include_industries:
        selected = selected[
            selected["industry"].fillna("").map(
                lambda value: any(
                    industry in value for industry in plan.include_industries
                )
            )
        ]
    if plan.exclude_industries:
        selected = selected[
            ~selected["industry"].fillna("").map(
                lambda value: any(
                    industry in value for industry in plan.exclude_industries
                )
            )
        ]
    if plan.include_symbols:
        selected = selected[selected["symbol"].isin(plan.include_symbols)]
    if plan.exclude_symbols:
        selected = selected[~selected["symbol"].isin(plan.exclude_symbols)]
    if plan.freshness_policy in {
        FreshnessPolicy.EXCLUDE_STALE,
        FreshnessPolicy.REQUIRE_FRESH,
    }:
        selected = selected[~selected["snapshot_stale"]]
    return selected.copy()


class UniverseFilter:
    @staticmethod
    def apply(
        frame: pd.DataFrame,
        plan: ScannerQueryPlan,
    ) -> FilterResult:
        selected = _universe_scope(frame, plan)
        selected["missing_filter_fields_internal"] = [
            [] for _ in range(len(selected))
        ]
        summary: dict[str, int] = {}
        for node in plan.filters:
            matched, missing = _node_mask(selected, node)
            missing_count = int(missing.sum())
            if missing_count:
                key = (
                    node.field.value
                    if hasattr(node, "field")
                    else "boolean_expression"
                )
                summary[key] = summary.get(key, 0) + missing_count
                selected.loc[missing, "missing_filter_fields_internal"] = selected.loc[
                    missing, "missing_filter_fields_internal"
                ].map(lambda values, field=key: [*values, field])
            if (
                plan.missing_data_policy
                == MissingDataPolicy.REQUIRE_CLARIFICATION
                and missing_count
            ):
                raise ValueError(
                    "filter field has missing values and the query requires "
                    "clarification"
                )
            if plan.missing_data_policy in {
                MissingDataPolicy.INCLUDE_WITH_FLAG,
                MissingDataPolicy.IGNORE_FILTER,
            }:
                matched |= missing
            selected = selected[matched].copy()
        return FilterResult(
            frame=selected,
            missing_data_summary=summary,
        )

    @staticmethod
    def partition(
        frame: pd.DataFrame,
        plan: ScannerQueryPlan,
        *,
        core_result: FilterResult | None = None,
    ) -> FilterLayers:
        """Partition the scoped universe; never promotes rows into ``core``."""

        formal = core_result or UniverseFilter.apply(frame, plan)
        scoped = _universe_scope(frame, plan)
        scoped["missing_filter_fields_internal"] = [
            [] for _ in range(len(scoped))
        ]
        if not plan.filters:
            empty = scoped.iloc[0:0].copy()
            return FilterLayers(
                core=formal.frame,
                near=empty,
                control=empty,
                missing_data_summary=formal.missing_data_summary,
            )

        strict_masks: list[pd.Series] = []
        near_masks: list[pd.Series] = []
        for node in plan.filters:
            matched, missing = _node_mask(scoped, node)
            if plan.missing_data_policy in {
                MissingDataPolicy.INCLUDE_WITH_FLAG,
                MissingDataPolicy.IGNORE_FILTER,
            }:
                matched |= missing
            strict_masks.append(matched)
            near_masks.append(_near_node_mask(scoped, node))

        near_mask = pd.Series(False, index=scoped.index)
        for index, near in enumerate(near_masks):
            candidate = near.copy()
            for other_index, strict in enumerate(strict_masks):
                if other_index != index:
                    candidate &= strict
            near_mask |= candidate

        core_indexes = formal.frame.index
        near_mask &= ~scoped.index.isin(core_indexes)
        near = scoped[near_mask].copy()
        control = scoped[
            ~scoped.index.isin(core_indexes) & ~scoped.index.isin(near.index)
        ].copy()
        return FilterLayers(
            core=formal.frame,
            near=near,
            control=control,
            missing_data_summary=formal.missing_data_summary,
        )


__all__ = ["FilterLayers", "FilterResult", "UniverseFilter"]
