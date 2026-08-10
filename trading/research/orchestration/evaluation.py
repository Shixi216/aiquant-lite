from __future__ import annotations

from trading.research.orchestration.repository import OrchestrationRepository
from trading.research.orchestration.schemas import (
    EvaluationRequest,
    EvaluationResponse,
)


class OrchestrationEvaluationService:
    """Append-only ex-post evaluation; it never mutates historical scores."""

    def __init__(self, repository: OrchestrationRepository) -> None:
        self.repository = repository

    def record(self, request: EvaluationRequest) -> EvaluationResponse:
        evaluation_id, inserted = self.repository.save_evaluation(request)
        return EvaluationResponse(
            evaluation_id=evaluation_id,
            persisted=inserted,
        )


__all__ = ["OrchestrationEvaluationService"]
