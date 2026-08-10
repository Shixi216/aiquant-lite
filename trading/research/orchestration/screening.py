from __future__ import annotations

import tracemalloc
from time import perf_counter

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.schemas import (
    ScreeningCandidate,
    ScreeningPerformance,
    ScreeningRequest,
    ScreeningResponse,
)
from trading.research.orchestration.shadow_scorer import score_shadow_bundle
from trading.schemas import AnalysisMode


class ScreeningEngine:
    """Local-only O(N log N) screen with one bulk database session."""

    def __init__(self, loader: FactorLoader) -> None:
        self.loader = loader

    @staticmethod
    def _ranked_factors(
        contributions: dict[str, float],
        *,
        positive: bool,
    ) -> list[FactorType]:
        selected = [
            (FactorType(key), value)
            for key, value in contributions.items()
            if (value > 0 if positive else value < 0)
        ]
        selected.sort(
            key=lambda item: (-item[1] if positive else item[1], item[0].value)
        )
        return [item[0] for item in selected[:3]]

    def run(self, request: ScreeningRequest) -> ScreeningResponse:
        tracemalloc.start()
        started = perf_counter()
        bundles, connection_count = self.loader.load_many(
            analysis_mode=AnalysisMode.SCREENING,
            data_cutoff=request.data_cutoff,
            symbols=request.symbols,
        )
        coverage_distribution = {f"{index}/5": 0 for index in range(6)}
        candidates: list[ScreeningCandidate] = []
        for bundle in bundles:
            coverage_distribution[bundle.factor_coverage.display] += 1
            if not bundle.available_factor_types:
                continue
            composite = score_shadow_bundle(bundle)
            candidates.append(
                ScreeningCandidate(
                    symbol=bundle.symbol,
                    shadow_score=composite.score,
                    composite_confidence=composite.confidence,
                    factor_coverage=bundle.factor_coverage,
                    available_factors=bundle.available_factor_types,
                    missing_factors=bundle.missing_factor_types,
                    top_positive_factors=self._ranked_factors(
                        composite.factor_contributions,
                        positive=True,
                    ),
                    top_negative_factors=self._ranked_factors(
                        composite.factor_contributions,
                        positive=False,
                    ),
                    risk_flags=composite.risk_flags,
                )
            )
        candidates.sort(
            key=lambda item: (
                -item.shadow_score,
                -item.composite_confidence,
                item.symbol,
            )
        )
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return ScreeningResponse(
            candidates=candidates[: request.limit],
            coverage_distribution=coverage_distribution,
            performance=ScreeningPerformance(
                symbol_count=len(bundles),
                elapsed_seconds=elapsed,
                peak_memory_bytes=peak,
                database_connection_count=connection_count,
            ),
            data_cutoff=request.data_cutoff,
        )


__all__ = ["ScreeningEngine"]
