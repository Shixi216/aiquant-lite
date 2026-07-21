from __future__ import annotations

from datetime import date

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from router.schemas.announcement_verification import (
    AnnouncementVerificationOutput,
)


class AnnouncementVerificationPipelineRequest(
    BaseModel
):
    """Request for exact official announcement verification."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(
        min_length=1,
        max_length=32,
    )
    record_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    announcement_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]{6,30}$",
    )
    start_date: date | None = Field(
        default=None,
        description=(
            "兼容字段；显式公告选择时不参与公告定位"
        ),
    )
    end_date: date | None = Field(
        default=None,
        description=(
            "兼容字段；显式公告选择时不参与公告定位"
        ),
    )
    claims: list[str] = Field(
        min_length=1,
        max_length=20,
    )
    max_tokens: int = Field(
        default=5000,
        ge=512,
        le=8192,
    )

    @model_validator(mode="after")
    def validate_request(
        self,
    ) -> AnnouncementVerificationPipelineRequest:
        if (
            self.record_id is None
            and self.announcement_id is None
        ):
            raise ValueError(
                "必须提供 record_id 或 announcement_id"
            )

        one_date_missing = (
            self.start_date is None
        ) != (
            self.end_date is None
        )

        if one_date_missing:
            raise ValueError(
                "start_date 和 end_date 必须同时提供"
                "或同时省略"
            )

        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError(
                "start_date 不能晚于 end_date"
            )

        return self


class AnnouncementVerificationPipelineResponse(
    BaseModel
):
    """Audited result from exact announcement verification."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start_date: date | None
    end_date: date | None
    record_id: str
    announcement_id: str
    announcement_date: str
    title: str
    source_url: str
    pdf_url: str
    pdf_sha256: str
    text_sha256: str
    task_id: str
    call_ids: list[str]
    attempts: int
    validated: bool
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, int] = Field(
        default_factory=dict
    )
    output: AnnouncementVerificationOutput