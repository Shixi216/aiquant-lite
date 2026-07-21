from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DataType(StrEnum):
    STOCK_BASIC = "stock_basic"
    REALTIME_QUOTE = "realtime_quote"
    DAILY_BAR = "daily_bar"
    FINANCIAL_STATEMENT = "financial_statement"
    ANNOUNCEMENT = "announcement"
    FINANCE_NEWS = "finance_news"


class SourceLevel(StrEnum):
    OFFICIAL = "official"
    STRUCTURED = "structured"
    PUBLIC_WEB = "public_web"
    MEDIA = "media"


class MarketRecord(BaseModel):
    """Unified record returned by all financial data providers."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    symbol: str
    data_type: DataType
    event_time: datetime
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now().astimezone()
    )

    source_name: str
    source_url: HttpUrl | None = None
    source_level: SourceLevel

    verified: bool = False
    content_hash: str | None = None
    data: dict[str, Any]