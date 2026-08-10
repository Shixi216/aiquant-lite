from __future__ import annotations

from dataclasses import dataclass

from trading.research.policy_news.models import EventBundle
from trading.research.policy_news.policy import (
    POLICY_NEWS_SOURCE_VERSION,
    source_authority_weights,
    verification_weights,
)
from trading.research.policy_news.schemas import (
    PolicyFactType,
    PolicyRiskFlag,
    PolicyVerificationStatus,
)


@dataclass(frozen=True)
class SourceAuthorityResult:
    source_level: str
    authority_weight: float
    fact_type: PolicyFactType
    verification_status: PolicyVerificationStatus
    verification_weight: float
    risk_flags: tuple[PolicyRiskFlag, ...]
    algorithm_version: str = POLICY_NEWS_SOURCE_VERSION


def _source_class(bundle: EventBundle) -> str:
    source = bundle.primary_source
    name = source.source_name.casefold()
    url = (source.source_url or "").casefold()
    payload = source.payload
    publisher = str(payload.get("publisher") or "").casefold()
    combined = " ".join((name, url, publisher))
    if any(
        marker in combined
        for marker in (
            "gov.cn",
            "国务院",
            "全国人大",
            "财政部",
            "国家发展改革委",
            "工业和信息化部",
            "商务部",
        )
    ):
        return "central_government"
    if any(
        marker in combined
        for marker in (
            "csrc.gov.cn",
            "证监会",
            "上交所",
            "深交所",
            "北交所",
            "samr.gov.cn",
            "国家市场监督管理",
        )
    ):
        return "regulator"
    if "gov.cn" in combined or "人民政府" in combined:
        return "local_government"
    if (
        bundle.cluster_event_type == "announcement"
        or "cninfo" in combined
        or source.source_level.casefold() == "official"
    ):
        return "company_announcement"
    if any(
        marker in combined
        for marker in (
            "新华社",
            "中国证券报",
            "上海证券报",
            "证券时报",
            "财联社",
            "第一财经",
        )
    ):
        return "authoritative_media"
    if source.source_level.casefold() == "media":
        return "media"
    if "analyst" in combined or "分析师" in combined or "研报" in combined:
        return "analyst"
    if "传闻" in combined or "rumor" in combined:
        return "rumor"
    return "unknown"


def _fact_type(source_class: str) -> PolicyFactType:
    return {
        "central_government": PolicyFactType.OFFICIAL_DOCUMENT,
        "regulator": PolicyFactType.REGULATORY_DISCLOSURE,
        "local_government": PolicyFactType.OFFICIAL_DOCUMENT,
        "company_announcement": PolicyFactType.COMPANY_ANNOUNCEMENT,
        "authoritative_media": PolicyFactType.VERIFIED_MEDIA_REPORT,
        "media": PolicyFactType.MEDIA_REPORT,
        "analyst": PolicyFactType.ANALYST_INTERPRETATION,
        "rumor": PolicyFactType.SPECULATION,
    }.get(source_class, PolicyFactType.UNKNOWN)


def rank_source(bundle: EventBundle) -> SourceAuthorityResult:
    source_class = _source_class(bundle)
    weights = source_authority_weights()
    flags: list[PolicyRiskFlag] = []
    primary = bundle.primary_source
    official = source_class in {
        "central_government",
        "regulator",
        "local_government",
        "company_announcement",
    }
    payloads = [record.payload for record in bundle.source_records]
    conflict = any(
        bool(payload.get("conflict") or payload.get("contradictory"))
        for payload in payloads
    )
    retracted = any(
        marker in bundle.canonical_title
        for marker in ("撤回", "撤销", "更正并撤回")
    )
    if retracted:
        status = PolicyVerificationStatus.RETRACTED
    elif conflict:
        status = PolicyVerificationStatus.CONFLICT
        flags.append(PolicyRiskFlag.EVENT_CONFLICT)
    elif bundle.source_count > 1:
        status = PolicyVerificationStatus.VERIFIED_MULTI_SOURCE
    elif official and primary.verified:
        status = PolicyVerificationStatus.SINGLE_OFFICIAL_SOURCE
    elif source_class in {"authoritative_media", "media"}:
        status = PolicyVerificationStatus.SINGLE_MEDIA_SOURCE
        flags.append(PolicyRiskFlag.SINGLE_MEDIA_SOURCE)
    else:
        status = PolicyVerificationStatus.UNVERIFIED
        flags.append(PolicyRiskFlag.UNVERIFIED_EVENT)
    if source_class == "unknown":
        flags.append(PolicyRiskFlag.SOURCE_UNKNOWN)
    if not primary.source_url:
        flags.append(PolicyRiskFlag.PRIMARY_SOURCE_MISSING)
    verification = verification_weights()[status.value]
    if status == PolicyVerificationStatus.RETRACTED:
        flags.append(PolicyRiskFlag.POLICY_RETRACTED)
    return SourceAuthorityResult(
        source_level=source_class,
        authority_weight=weights[source_class],
        fact_type=_fact_type(source_class),
        verification_status=status,
        verification_weight=verification,
        risk_flags=tuple(sorted(set(flags), key=lambda item: item.value)),
    )


__all__ = ["SourceAuthorityResult", "rank_source"]
