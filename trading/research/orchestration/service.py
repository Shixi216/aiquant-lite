from __future__ import annotations

from datetime import datetime

from trading.research.orchestration.decision import DecisionShadowEngine
from trading.research.orchestration.evaluation import (
    OrchestrationEvaluationService,
)
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.repository import OrchestrationRepository
from trading.research.orchestration.research import ResearchEngine
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    DecisionShadowResponse,
    EvaluationRequest,
    EvaluationResponse,
    ResearchRequest,
    ResearchResponse,
    ScreeningRequest,
    ScreeningResponse,
    SymbolOrchestrationResponse,
)
from trading.research.orchestration.screening import ScreeningEngine
from trading.research.orchestration.shadow_scorer import score_shadow_bundle
from trading.research.overheat_preproduction_shadow import (
    PreproductionOverheatShadowService,
)
from trading.schemas import AnalysisMode


class OrchestrationService:
    def __init__(
        self,
        repository: OrchestrationRepository | None = None,
    ) -> None:
        self.repository = repository or OrchestrationRepository()
        self.loader = FactorLoader(self.repository)
        self.screening_engine = ScreeningEngine(self.loader)
        self.research_engine = ResearchEngine(
            self.loader,
            self.repository,
        )
        self.overheat_shadow_service = PreproductionOverheatShadowService()
        self.decision_engine = DecisionShadowEngine(
            self.loader,
            self.repository,
            self.overheat_shadow_service,
        )
        self.evaluation_service = OrchestrationEvaluationService(
            self.repository
        )

    @staticmethod
    def _validate_cutoff(data_cutoff: datetime) -> None:
        if data_cutoff > datetime.now().astimezone():
            raise ValueError("data_cutoff must not be in the future")

    def screen(self, request: ScreeningRequest) -> ScreeningResponse:
        self._validate_cutoff(request.data_cutoff)
        return self.screening_engine.run(request)

    def research(self, request: ResearchRequest) -> ResearchResponse:
        self._validate_cutoff(request.data_cutoff)
        return self.research_engine.run(request)

    def decision_shadow(
        self,
        request: DecisionShadowRequest,
    ) -> DecisionShadowResponse:
        self._validate_cutoff(request.data_cutoff)
        return self.decision_engine.run(request)

    def symbol(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        analysis_mode: AnalysisMode,
    ) -> SymbolOrchestrationResponse:
        self._validate_cutoff(data_cutoff)
        bundles, _ = self.loader.load_many(
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
            symbols=[symbol],
        )
        if not bundles or not bundles[0].available_factor_types:
            raise ValueError("symbol has no usable orchestration factors")
        bundle = bundles[0]
        return SymbolOrchestrationResponse(
            analysis_mode=analysis_mode,
            bundle=bundle,
            shadow_composite=score_shadow_bundle(bundle),
        )

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        return self.evaluation_service.record(request)


__all__ = ["OrchestrationService"]
