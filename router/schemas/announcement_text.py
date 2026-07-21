from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementTextDocument(BaseModel):
    """Extracted text layer of an announcement PDF."""

    model_config = ConfigDict(extra="forbid")

    announcement_id: str
    announcement_time: str
    source_pdf_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    text_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    pdf_path: str
    text_path: str
    metadata_path: str
    page_count: int = Field(gt=0)
    page_char_counts: list[int]
    text_length: int = Field(ge=0)
    extract_method: Literal["pymupdf_text"]
    requires_ocr: bool
    cache_hit: bool
    extracted_at: str
    text: str