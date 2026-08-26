from __future__ import annotations

import logging
from typing import Any

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.factor_loader import FactorLoader
from trading.research.orchestration.models import (
    FORMAL_STRATEGY_VERSION,
    FORMAL_THREE_FACTOR_STRATEGY_VERSION,
    FORMAL_THREE_FACTOR_WEIGHTS,
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


logger = logging.getLogger(__name__)


def formal_result(
    request: DecisionShadowRequest,
) -> FormalResult:
    if request.custom_formal_weights is not None:
        weights = {}
        for k, v in request.custom_formal_weights.items():
            try:
                weights[FactorType(k.upper())] = v
            except ValueError:
                pass
        strategy_version = FORMAL_THREE_FACTOR_STRATEGY_VERSION
    elif request.sentiment_score is not None:
        weights = dict(FORMAL_THREE_FACTOR_WEIGHTS)
        strategy_version = FORMAL_THREE_FACTOR_STRATEGY_VERSION
    else:
        weights = dict(FORMAL_WEIGHTS)
        strategy_version = FORMAL_STRATEGY_VERSION

    total_w = sum(weights.values())
    norm_weights = (
        {k: v / total_w for k, v in weights.items()}
        if total_w > 0
        else dict(FORMAL_WEIGHTS)
    )

    score = 0.0
    confidence = 0.0

    w_tech = norm_weights.get(FactorType.TECHNICAL, 0.0)
    score += request.technical_score * w_tech
    confidence += request.technical_confidence * w_tech

    w_fund = norm_weights.get(FactorType.FUNDAMENTAL, 0.0)
    score += request.fundamental_score * w_fund
    confidence += request.fundamental_confidence * w_fund

    w_sent = norm_weights.get(FactorType.SENTIMENT, 0.0)
    if w_sent > 0 and request.sentiment_score is not None:
        score += request.sentiment_score * w_sent
        sent_conf = (
            request.sentiment_confidence
            if request.sentiment_confidence is not None
            else 0.5
        )
        confidence += sent_conf * w_sent

    if score >= 0.20:
        proposal_action = Action.BUY
    elif score <= -0.20:
        proposal_action = Action.SELL
    else:
        proposal_action = Action.HOLD
    final_action = Action.VETO if request.hard_veto else proposal_action
    return FormalResult(
        strategy_version=strategy_version,
        score=max(-1.0, min(1.0, score)),
        confidence=max(0.0, min(1.0, confidence)),
        proposal_action=proposal_action,
        final_action=final_action,
        hard_veto=request.hard_veto,
        formal_weights={
            key.value: value for key, value in norm_weights.items()
        },
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
        if self.overheat_shadow_recorder is not None:
            try:
                self.overheat_shadow_recorder.record(
                    request,
                    formal,
                    formal_result,
                )
            except Exception:
                logger.exception(
                    "preproduction overheat shadow recording failed"
                )
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
