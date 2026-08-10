from __future__ import annotations

from collections.abc import Callable

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.repository import OrchestrationRepository
from trading.research.orchestration.schemas import (
    OrchestrationRiskFlag,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from trading.research.orchestration.shadow_scorer import score_shadow_bundle
from trading.schemas import AnalysisMode


ControlledFetcher = Callable[
    [list[str], list[FactorType]],
    int,
]


class ResearchEngine:
    def __init__(
        self,
        loader: FactorLoader,
        repository: OrchestrationRepository,
        *,
        controlled_fetcher: ControlledFetcher | None = None,
    ) -> None:
        self.loader = loader
        self.repository = repository
        self.controlled_fetcher = controlled_fetcher

    def run(self, request: ResearchRequest) -> ResearchResponse:
        bundles, _ = self.loader.load_many(
            analysis_mode=AnalysisMode.RESEARCH,
            data_cutoff=request.data_cutoff,
            symbols=request.symbols,
        )
        network_requests = 0
        if request.allow_external_fetch and self.controlled_fetcher is not None:
            missing_types = sorted(
                {
                    factor_type
                    for bundle in bundles
                    for factor_type in bundle.missing_factor_types
                },
                key=lambda item: item.value,
            )
            if missing_types:
                network_requests = self.controlled_fetcher(
                    request.symbols,
                    missing_types,
                )
                bundles, _ = self.loader.load_many(
                    analysis_mode=AnalysisMode.RESEARCH,
                    data_cutoff=request.data_cutoff,
                    symbols=request.symbols,
                )

        results: list[ResearchResult] = []
        for bundle in bundles:
            factor_map = bundle.factor_map()
            if request.persist:
                updated = {}
                for factor_type, factor in factor_map.items():
                    if factor.source_factor_output_id is not None:
                        updated[factor_type] = factor
                        continue
                    persisted = self.repository.save_factor(
                        self.loader.persistable_factor(
                            bundle.symbol,
                            factor,
                        )
                    )
                    updated[factor_type] = factor.model_copy(
                        update={
                            "factor_output_id": persisted.factor_id,
                            "source_factor_output_id": persisted.factor_id,
                        }
                    )
                bundle = self.loader.build_bundle(
                    symbol=bundle.symbol,
                    analysis_mode=AnalysisMode.RESEARCH,
                    data_cutoff=request.data_cutoff,
                    factors=updated,
                )
            composite = score_shadow_bundle(bundle)
            if request.persist:
                self.repository.save_bundle_and_composite(bundle, composite)
            risk_flags = list(composite.risk_flags)
            if (
                request.allow_external_fetch
                and self.controlled_fetcher is None
            ):
                risk_flags.append(OrchestrationRiskFlag.MODE_RESTRICTION)
            results.append(
                ResearchResult(
                    symbol=bundle.symbol,
                    shadow_composite=composite,
                    factor_coverage=bundle.factor_coverage,
                    available_factors=bundle.available_factor_types,
                    missing_factors=bundle.missing_factor_types,
                    suggested_capture_items=bundle.missing_factor_types,
                    evidence_ids=bundle.evidence_ids,
                    risk_flags=list(dict.fromkeys(risk_flags)),
                    persisted=request.persist,
                )
            )
        return ResearchResponse(
            data_cutoff=request.data_cutoff,
            results=results,
            network_request_count=network_requests,
            model_call_count=0,
        )


__all__ = ["ResearchEngine"]
