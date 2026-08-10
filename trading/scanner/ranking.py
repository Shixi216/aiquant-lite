from __future__ import annotations

from typing import Any

import pandas as pd

from trading.scanner.models import RANKING_WEIGHTS, RankingWeights, SortDirection
from trading.scanner.schemas import ScannerQueryPlan


def _normalized_score(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else (float(value) + 1) / 2


class ScannerRanker:
    def __init__(self, weights: RankingWeights = RANKING_WEIGHTS) -> None:
        self.weights = weights

    def _components(self, row: Any) -> dict[str, float | None]:
        anomaly_count = len(row.anomaly_types)
        risk_count = len(row.local_risk_flags)
        technical = _normalized_score(row.technical_score)
        capital = _normalized_score(row.capital_flow_score)
        shadow = _normalized_score(row.shadow_composite_score)
        confidence = (
            0.0
            if row.composite_confidence is None
            or pd.isna(row.composite_confidence)
            else float(row.composite_confidence)
        )
        return {
            "query_match": 1.0,
            "anomaly_strength": min(1.0, anomaly_count / 4),
            "technical": technical,
            "capital_flow": capital,
            "shadow_composite": shadow,
            "factor_coverage": float(row.factor_coverage_ratio),
            "confidence": confidence,
            "liquidity": float(row.amount_market_percentile),
            "freshness": 0.5 if bool(row.snapshot_stale) else 1.0,
            "risk_penalty": min(
                self.weights.maximum_risk_penalty,
                risk_count * 0.05,
            ),
        }

    def _score(self, components: dict[str, float | None]) -> float:
        weights = self.weights
        total = (
            components["query_match"] * weights.query_match
            + components["anomaly_strength"] * weights.anomaly_strength
            + (components["technical"] or 0.0) * weights.technical
            + (components["capital_flow"] or 0.0) * weights.capital_flow
            + (components["shadow_composite"] or 0.0)
            * weights.shadow_composite
            + components["factor_coverage"] * weights.factor_coverage
            + components["confidence"] * weights.confidence
            + components["liquidity"] * weights.liquidity
            + components["freshness"] * weights.freshness
            - components["risk_penalty"]
        )
        return max(0.0, min(1.0, float(total)))

    def rank(
        self,
        frame: pd.DataFrame,
        plan: ScannerQueryPlan,
    ) -> pd.DataFrame:
        output = frame.copy()
        components = [
            self._components(row) for row in output.itertuples(index=False)
        ]
        output["score_components"] = components
        output["scanner_score"] = [self._score(item) for item in components]
        sort_columns = [node.field.value for node in plan.sort_fields]
        ascending = [
            node.direction == SortDirection.ASC for node in plan.sort_fields
        ]
        sort_columns.extend(["scanner_score", "symbol"])
        ascending.extend([False, True])
        return output.sort_values(
            sort_columns,
            ascending=ascending,
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)


__all__ = ["ScannerRanker"]
