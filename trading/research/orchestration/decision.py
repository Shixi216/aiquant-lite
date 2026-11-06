from __future__ import annotations

from typing import Any

from trading.decision_support.formal_score_resolver import FormalScoreResolver
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.models import (
    FORMAL_STRATEGY_VERSION,
    FORMAL_WEIGHTS,
)
from trading.research.orchestration.repository import OrchestrationRepository
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    DecisionShadowResponse,
    FactorBundle,
    FormalResult,
)
from trading.research.orchestration.shadow_scorer import score_shadow_bundle
from trading.schemas import Action, AnalysisMode


def formal_result(
    request: DecisionShadowRequest,
) -> FormalResult:
    # Request scores and experimental weights cannot override the formal snapshot.
    resolved = FormalScoreResolver().resolve(
        request.symbol, request.technical_score, request.fundamental_score,
        data_cutoff=request.data_cutoff, with_detail=True,
    )
    score = resolved.score
    if resolved.degraded:
        proposal_action = Action.HOLD
    elif score >= 0.20:
        proposal_action = Action.BUY
    elif score <= -0.20:
        proposal_action = Action.SELL
    else:
        proposal_action = Action.HOLD
    return FormalResult(
        strategy_version=FORMAL_STRATEGY_VERSION,
        status="UNAVAILABLE" if resolved.degraded else "AVAILABLE",
        score_source=resolved.source,
        reason="No point-in-time four-dimensional snapshot; no formal action" if resolved.degraded else "",
        score=score,
        confidence=0.0 if resolved.degraded else min(
            request.technical_confidence, request.fundamental_confidence,
        ),
        proposal_action=proposal_action,
        final_action=Action.VETO if request.hard_veto else proposal_action,
        hard_veto=request.hard_veto,
        formal_weights={key.value: value for key, value in FORMAL_WEIGHTS.items()},
    )


class DecisionShadowEngine:
    def __init__(
        self,
        loader: FactorLoader,
        repository: OrchestrationRepository,
        overheat_shadow_recorder: Any | None = None,
    ) -> None:
        self.loader = loader
        self.repository = repository
        self.overheat_shadow_recorder = overheat_shadow_recorder

    def run_with_bundle(
        self,
        request: DecisionShadowRequest,
    ) -> tuple[DecisionShadowResponse, FactorBundle]:
        bundles, _ = self.loader.load_many(
            analysis_mode=AnalysisMode.DECISION,
            data_cutoff=request.data_cutoff,
            symbols=[request.symbol],
        )
        if not bundles:
            raise ValueError("symbol has no point-in-time orchestration input")
        bundle = bundles[0]
        composite = score_shadow_bundle(bundle)
        if request.persist_shadow:
            self.repository.save_bundle_and_composite(bundle, composite)
        formal = formal_result(request)
        # Frozen overheat experiment perturbs raw technical scores. Its 60/40
        # observations must not be mixed with the new rank-based formal baseline.
        response = DecisionShadowResponse(
            formal_result=formal,
            shadow_composite=composite,
            formal_weights=formal.formal_weights,
            effective_shadow_weights=composite.effective_weights,
            factor_coverage=bundle.factor_coverage,
            available_factors=bundle.available_factor_types,
            missing_factors=bundle.missing_factor_types,
            correlation_discounts=composite.correlation_discounts,
            evidence_ids=bundle.evidence_ids,
            risk_flags=composite.risk_flags,
        )
        return response, bundle

    def run(
        self,
        request: DecisionShadowRequest,
    ) -> DecisionShadowResponse:
        response, _ = self.run_with_bundle(request)
        return response


__all__ = ["DecisionShadowEngine", "formal_result"]
