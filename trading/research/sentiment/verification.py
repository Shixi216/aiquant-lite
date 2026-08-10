from __future__ import annotations

from dataclasses import dataclass

from trading.research.sentiment.models import EventBundle
from trading.research.sentiment.policy import (
    VERIFICATION_VERSION,
    verification_weights,
)
from trading.research.sentiment.schemas import (
    SentimentRiskFlag,
    SentimentVerificationStatus,
)


@dataclass(frozen=True)
class VerificationResult:
    status: SentimentVerificationStatus
    weight: float
    risk_flags: tuple[SentimentRiskFlag, ...]
    algorithm_version: str = VERIFICATION_VERSION


def verification_result(bundle: EventBundle) -> VerificationResult:
    title = bundle.canonical_title
    payload_statuses = {
        str(source.payload.get("verification_status") or "").casefold()
        for source in bundle.source_records
    }
    if any(word in title for word in ("撤回", "撤销", "作废")):
        status = SentimentVerificationStatus.RETRACTED
    elif payload_statuses & {"conflict", "contradictory", "disputed"}:
        status = SentimentVerificationStatus.CONFLICT
    elif bundle.source_count > 1 and all(
        source.verified for source in bundle.source_records
    ):
        status = SentimentVerificationStatus.VERIFIED_MULTI_SOURCE
    elif (
        bundle.primary_source.source_level.casefold() == "official"
        and bundle.primary_source.verified
    ):
        status = SentimentVerificationStatus.VERIFIED_OFFICIAL
    elif bundle.primary_source.source_level.casefold() == "official":
        status = SentimentVerificationStatus.SINGLE_OFFICIAL_SOURCE
    elif bundle.source_count == 1:
        status = SentimentVerificationStatus.SINGLE_MEDIA_SOURCE
    else:
        status = SentimentVerificationStatus.UNVERIFIED

    flags: list[SentimentRiskFlag] = []
    if status == SentimentVerificationStatus.SINGLE_MEDIA_SOURCE:
        flags.append(SentimentRiskFlag.SINGLE_MEDIA_SOURCE)
    elif status == SentimentVerificationStatus.CONFLICT:
        flags.append(SentimentRiskFlag.EVENT_CONFLICT)
    elif status == SentimentVerificationStatus.UNVERIFIED:
        flags.append(SentimentRiskFlag.UNVERIFIED_EVENT)
    elif status == SentimentVerificationStatus.RETRACTED:
        flags.append(SentimentRiskFlag.RETRACTED_EVENT)
    return VerificationResult(
        status=status,
        weight=verification_weights()[status.value],
        risk_flags=tuple(flags),
    )


__all__ = ["VerificationResult", "verification_result"]
