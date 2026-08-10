from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResearchFactorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    observation: str = Field(min_length=1, max_length=1000)


class ResearchSynthesisOutput(BaseModel):
    """Research-only model synthesis; never a formal trading decision."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=3000)
    factor_observations: list[ResearchFactorObservation]
    missing_data: list[str]
    risk_flags: list[str]
    confidence: float = Field(ge=0, le=1)
    not_trade_recommendation: bool

