from __future__ import annotations

from typing import Any, Callable

from router.integration.identifiers import new_request_id
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    ResearchRequest,
)
from trading.research.orchestration.service import OrchestrationService
from trading.scanner.schemas import (
    ScannerParseRequest,
    ScannerScanRequest,
)
from trading.scanner.service import MarketScannerService

from router.integration.schemas import (
    DecisionWorkflowRequest,
    DecisionWorkflowResult,
    ParseWorkflowRequest,
    ParseWorkflowResult,
    ResearchWorkflowRequest,
    ResearchWorkflowResult,
    ScanWorkflowRequest,
    ScanWorkflowResult,
    WorkflowContext,
    WorkflowStep,
)
from router.integration.sanitization import sanitize_error


ScannerFactory = Callable[[], MarketScannerService]
OrchestrationFactory = Callable[[], OrchestrationService]

class InteractionWorkflowService:
    """Deterministic SCREENING -> RESEARCH -> confirmed DECISION boundary."""

    def __init__(
        self,
        *,
        scanner_factory: ScannerFactory = MarketScannerService,
        orchestration_factory: OrchestrationFactory = OrchestrationService,
    ) -> None:
        self._scanner_factory = scanner_factory
        self._orchestration_factory = orchestration_factory

    @staticmethod
    def _dump(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        raise TypeError("workflow result is not serializable")

    def parse(
        self,
        request: ParseWorkflowRequest,
        *,
        request_id: str,
    ) -> ParseWorkflowResult:
        result = self._scanner_factory().parse(
            ScannerParseRequest(
                query=request.query,
                top_n=request.top_n,
                data_cutoff=request.data_cutoff,
                allow_parser_model=False,
            )
        )
        return ParseWorkflowResult(
            workflow=WorkflowContext(
                step=WorkflowStep.PARSE,
                network_request_count=0,
                model_call_count=result.parser_model_call_count,
            ),
            parsed_query=(
                result.parsed_query.model_dump(mode="json")
                if result.parsed_query is not None
                else None
            ),
            condition_summary=result.condition_summary,
            clarification_required=result.clarification_required,
            clarification_questions=result.clarification_questions,
            unsupported_fragments=result.unsupported_fragments,
        )

    def scan(
        self,
        request: ScanWorkflowRequest,
        *,
        request_id: str,
    ) -> ScanWorkflowResult:
        result = self._scanner_factory().scan(
            ScannerScanRequest(
                query=request.query,
                top_n=request.top_n,
                data_cutoff=request.data_cutoff,
                allow_parser_model=False,
                persist_run=False,
            )
        )
        payload = self._dump(result)
        performance = payload.get("performance") or {}
        return ScanWorkflowResult(
            workflow=WorkflowContext(
                step=WorkflowStep.SCAN,
                parent_request_id=request.parent_request_id,
                network_request_count=int(
                    performance.get(
                        "network_request_count",
                        payload.get("network_request_count", 0),
                    )
                ),
                model_call_count=int(
                    performance.get(
                        "model_call_count",
                        payload.get("model_call_count", 0),
                    )
                ),
            ),
            scan=payload,
        )

    def research(
        self,
        request: ResearchWorkflowRequest,
        *,
        request_id: str,
    ) -> ResearchWorkflowResult:
        result = self._orchestration_factory().research(
            ResearchRequest(
                symbols=request.symbols,
                data_cutoff=request.data_cutoff,
                persist=request.capture_snapshot,
                allow_external_fetch=False,
            )
        )
        return ResearchWorkflowResult(
            workflow=WorkflowContext(
                step=WorkflowStep.RESEARCH,
                parent_request_id=request.parent_request_id,
                network_request_count=result.network_request_count,
                model_call_count=result.model_call_count,
            ),
            research=result.model_dump(mode="json"),
            ai_deep_analysis_symbols=request.symbols[
                : request.ai_deep_analysis_limit
            ],
        )

    def decision(
        self,
        request: DecisionWorkflowRequest,
        *,
        request_id: str,
    ) -> DecisionWorkflowResult:
        result = self._orchestration_factory().decision_shadow(
            DecisionShadowRequest(
                symbol=request.symbol,
                data_cutoff=request.data_cutoff,
                technical_score=request.technical_score,
                technical_confidence=request.technical_confidence,
                fundamental_score=request.fundamental_score,
                fundamental_confidence=request.fundamental_confidence,
                hard_veto=request.hard_veto,
                persist_shadow=False,
            )
        )
        missing_factors = [
            item.value for item in result.missing_factors
        ]
        formal_payload = result.formal_result.model_dump(mode="json")
        if "FUNDAMENTAL" in missing_factors:
            formal_payload = {
                "status": "INSUFFICIENT_COVERAGE",
                "strategy_version": result.formal_result.strategy_version,
                "score": None,
                "confidence": None,
                "proposal_action": None,
                "final_action": (
                    "veto" if request.hard_veto else None
                ),
                "hard_veto": request.hard_veto,
                "formal_weights": result.formal_weights,
                "shadow_used_for_action": False,
                "shadow_used_for_veto": False,
                "reason": (
                    "point-in-time fundamental input is missing; "
                    "no formal 60/40 action was produced"
                ),
            }
        else:
            formal_payload["status"] = "AVAILABLE"
        return DecisionWorkflowResult(
            workflow=WorkflowContext(
                step=WorkflowStep.DECISION,
                parent_request_id=request.parent_request_id,
                decision_called=True,
            ),
            formal_result=formal_payload,
            shadow_composite=result.shadow_composite.model_dump(mode="json"),
            factor_coverage=result.factor_coverage.model_dump(mode="json"),
            available_factors=[
                item.value for item in result.available_factors
            ],
            missing_factors=missing_factors,
            evidence_ids=result.evidence_ids,
            formal_weights=result.formal_weights,
        )


__all__ = [
    "InteractionWorkflowService",
    "new_request_id",
    "sanitize_error",
]
