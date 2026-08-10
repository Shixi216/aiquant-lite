from __future__ import annotations

from dataclasses import dataclass

from trading.research.sentiment.models import EventBundle
from trading.research.sentiment.policy import (
    SOURCE_QUALITY_VERSION,
    source_quality_weights,
)
from trading.research.sentiment.schemas import (
    SentimentEventType,
    SentimentRiskFlag,
)


_AUTHORITATIVE_PUBLISHERS = (
    "新华社",
    "中国证券报",
    "上海证券报",
    "证券时报",
    "证券日报",
    "央广财经",
    "第一财经",
    "财联社",
)


@dataclass(frozen=True)
class SourceQualityResult:
    category: str
    weight: float
    source_level: str
    risk_flags: tuple[SentimentRiskFlag, ...]
    algorithm_version: str = SOURCE_QUALITY_VERSION


def source_quality(
    bundle: EventBundle,
    *,
    event_type: SentimentEventType,
) -> SourceQualityResult:
    primary = bundle.primary_source
    level = primary.source_level.strip().casefold()
    publisher = str(primary.payload.get("publisher") or "")
    combined = f"{primary.source_name} {publisher}"
    weights = source_quality_weights()

    if event_type == SentimentEventType.MARKET_RUMOR:
        category = "rumor"
    elif level == "official":
        category = "official"
    elif any(name in combined for name in _AUTHORITATIVE_PUBLISHERS):
        category = "authoritative_media"
    elif level in {"media", "public_web", "structured"}:
        category = "media"
    else:
        category = "unknown"

    flags: tuple[SentimentRiskFlag, ...] = ()
    if category in {"unknown", "rumor"}:
        flags = (SentimentRiskFlag.SOURCE_UNKNOWN,)
    return SourceQualityResult(
        category=category,
        weight=weights[category],
        source_level=level or "unknown",
        risk_flags=flags,
    )


__all__ = ["SourceQualityResult", "source_quality"]
