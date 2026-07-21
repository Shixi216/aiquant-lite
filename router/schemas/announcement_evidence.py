from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementEvidencePage(BaseModel):
    """One page of verified official announcement text."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    char_count: int = Field(ge=0)
    text: str


class AnnouncementEvidenceBundle(BaseModel):
    """Traceable evidence package for announcement verification."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    record_id: str
    title: str
    short_name: str | None
    announcement_id: str
    announcement_date: str
    source_name: str
    source_url: str
    source_level: str
    verified: bool
    source_authenticity: Literal["verified_official"]
    pdf_url: str
    pdf_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    text_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    page_count: int = Field(gt=0)
    text_length: int = Field(ge=0)
    requires_ocr: bool
    pages: list[AnnouncementEvidencePage]