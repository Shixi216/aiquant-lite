from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


VerificationVerdict = Literal[
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
]

TemporalNature = Literal[
    "HISTORICAL_FACT",
    "CURRENT_STATUS",
    "COMPANY_ESTIMATE",
    "FORWARD_LOOKING",
    "UNKNOWN",
]


class AnnouncementEvidenceCitation(BaseModel):
    """Exact quotation from one announcement page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    quote: str = Field(
        min_length=4,
        max_length=800,
    )


class AnnouncementVerifiedClaim(BaseModel):
    """One independently verifiable claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(
        pattern=r"^claim_[0-9]+$"
    )
    claim: str = Field(
        min_length=1,
        max_length=1000,
    )
    verdict: VerificationVerdict
    temporal_nature: TemporalNature
    normalized_fact: str | None = Field(
        default=None,
        max_length=1500,
    )
    evidence: list[
        AnnouncementEvidenceCitation
    ] = Field(
        default_factory=list,
        max_length=5,
    )
    reason: str = Field(
        min_length=1,
        max_length=1500,
    )
    confidence: float = Field(
        ge=0,
        le=1,
    )


class AnnouncementVerificationOutput(BaseModel):
    """Validated output of announcement_verifier."""

    model_config = ConfigDict(extra="forbid")

    announcement_id: str = Field(
        pattern=r"^[0-9]{6,30}$"
    )
    title: str = Field(
        min_length=1,
        max_length=500,
    )
    overall_verdict: VerificationVerdict
    summary: str = Field(
        min_length=1,
        max_length=2000,
    )
    claims: list[
        AnnouncementVerifiedClaim
    ] = Field(
        min_length=1,
        max_length=20,
    )
    warnings: list[str] = Field(
        default_factory=list,
        max_length=10,
    )