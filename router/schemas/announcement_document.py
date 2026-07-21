from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementDocument(BaseModel):
    """Validated local copy of an official announcement PDF."""

    model_config = ConfigDict(extra="forbid")

    announcement_id: str = Field(
        pattern=r"^[0-9]{6,30}$"
    )
    announcement_time: str
    source_url: str
    pdf_url: str
    local_path: str
    metadata_path: str
    content_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    cache_hit: bool
    fetched_at: str