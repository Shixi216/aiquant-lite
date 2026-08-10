from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from router.schemas import RouterInvokeResponse
from trading.research.policy_news.schemas import (
    ModelPolicyExtraction,
    PolicyRiskFlag,
    SchemaValidationStatus,
)
from trading.research.sentiment.models import EventBundle, EventSourceRecord


class PolicyNewsInternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyExtractionResult(PolicyNewsInternalModel):
    extraction: ModelPolicyExtraction
    model_call_ids: list[str] = Field(default_factory=list)
    validation_status: SchemaValidationStatus
    risk_flags: list[PolicyRiskFlag] = Field(default_factory=list)
    used_model: bool = False
    provider: str | None = None


class PolicyNewsModelProvider(Protocol):
    provider_name: str

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse: ...


__all__ = [
    "EventBundle",
    "EventSourceRecord",
    "PolicyExtractionResult",
    "PolicyNewsModelProvider",
]
