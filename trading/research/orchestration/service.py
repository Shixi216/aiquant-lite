from __future__ import annotations

from datetime import datetime
import logging
import time

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


logger = logging.getLogger(__name__)


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

    def decision_shadow_with_canonical_audit(
        self,
        request: DecisionShadowRequest,
        *,
        request_id: str,
        user_context=None,
    ) -> DecisionShadowResponse:
        from trading.decision_support.canonical_shadow import (
            CanonicalDecisionShadowRepository,
            CanonicalDecisionShadowService,
            runtime_shadow_record_source,
        )
        from trading.decision_support.canonical_input import (
            CanonicalVetoContext,
        )
        from trading.decision_support.formal_veto import evaluate_formal_veto
        from trading.decision_support.user_context import UserContext

        self._validate_cutoff(request.data_cutoff)
        context = user_context or UserContext.finance_mcp_shadow()
        shadow_repository = CanonicalDecisionShadowRepository()
        legacy_started = time.perf_counter()
        try:
            legacy, bundle = self.decision_engine.run_with_bundle(request)
        except Exception as exc:
            legacy_latency_ms = (time.perf_counter() - legacy_started) * 1000
            try:
                shadow_repository.save_error_observation(
                    request_id=request_id,
                    user_context=context,
                    request=request,
                    legacy_result=None,
                    legacy_latency_ms=legacy_latency_ms,
                    canonical_latency_ms=0.0,
                    record_source=runtime_shadow_record_source(),
                    legacy_error=type(exc).__name__,
                )
            except Exception:
                logger.exception("legacy failure shadow audit recording failed")
            raise
        legacy_latency_ms = (time.perf_counter() - legacy_started) * 1000
        canonical_started = time.perf_counter()
        try:
            risk_input = self.repository.load_formal_risk_input(
                symbol=request.symbol,
                data_cutoff=request.data_cutoff,
            )
            veto = evaluate_formal_veto(risk_input)
            veto_context = CanonicalVetoContext(
                result=veto.result,
                source=veto.source,
                reason=veto.reason,
                input_complete=veto.input_complete,
            )
            data = self.repository.load_bulk(
                data_cutoff=request.data_cutoff,
                symbols=[request.symbol],
            )
            evidence_ids = set(
                bundle.technical.market_record_ids
                if bundle.technical is not None
                else []
            )
            market_bars = [
                bar
                for bar in data.bars_by_symbol.get(request.symbol, [])
                if bar.canonical_record_id in evidence_ids
            ]
            CanonicalDecisionShadowService(
                repository=shadow_repository,
            ).run_and_record(
                request_id=request_id,
                user_context=context,
                request=request,
                bundle=bundle,
                market_bars=market_bars,
                legacy_result=legacy.formal_result,
                veto_context=veto_context,
                veto_event_count=veto.event_count,
                legacy_latency_ms=legacy_latency_ms,
                canonical_started_at=canonical_started,
                record_source=runtime_shadow_record_source(),
            )
        except Exception as exc:
            canonical_latency_ms = (
                time.perf_counter() - canonical_started
            ) * 1000
            logger.exception("canonical DecisionEngine shadow audit failed")
            try:
                shadow_repository.save_error_observation(
                    request_id=request_id,
                    user_context=context,
                    request=request,
                    legacy_result=legacy.formal_result,
                    legacy_latency_ms=legacy_latency_ms,
                    canonical_latency_ms=canonical_latency_ms,
                    record_source=runtime_shadow_record_source(),
                    canonical_error=type(exc).__name__,
                )
            except Exception:
                logger.exception("canonical error observation recording failed")
        return legacy

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
