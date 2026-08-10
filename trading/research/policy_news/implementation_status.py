from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading.research.policy_news.policy import (
    POLICY_NEWS_IMPLEMENTATION_VERSION,
    implementation_weights,
)
from trading.research.policy_news.schemas import (
    ImplementationStatus,
    PolicyEventCategory,
    PolicyRiskFlag,
)


@dataclass(frozen=True)
class ImplementationResult:
    status: ImplementationStatus
    confidence: float
    weight: float
    implementation_time: datetime | None
    termination_time: datetime | None
    risk_flags: tuple[PolicyRiskFlag, ...]
    algorithm_version: str = POLICY_NEWS_IMPLEMENTATION_VERSION


_STATUS_PATTERNS: tuple[
    tuple[tuple[str, ...], ImplementationStatus, float],
    ...,
] = (
    (("撤回", "撤销", "收回"), ImplementationStatus.RETRACTED, 0.95),
    (("终止", "终结", "不再实施"), ImplementationStatus.TERMINATED, 0.95),
    (("暂停", "暂缓", "中止"), ImplementationStatus.SUSPENDED, 0.90),
    (("已经完成", "实施完毕", "执行完毕"), ImplementationStatus.EXECUTED, 0.90),
    (("正式实施", "开始实施", "正在实施", "生效"), ImplementationStatus.IMPLEMENTING, 0.85),
    (("获批", "批准", "核准", "批复"), ImplementationStatus.APPROVED, 0.85),
    (("征求意见", "公开征求"), ImplementationStatus.CONSULTATION, 0.95),
    (("草案", "拟订", "拟制定"), ImplementationStatus.DRAFT, 0.90),
    (("传闻", "据传", "市场消息"), ImplementationStatus.RUMOR, 0.80),
    (("发布", "公告", "公布", "印发"), ImplementationStatus.ANNOUNCED, 0.70),
)


def classify_implementation(
    *,
    text: str,
    event_category: PolicyEventCategory,
    event_time: datetime,
    official_source: bool,
) -> ImplementationResult:
    normalized = text.strip()
    status = ImplementationStatus.UNKNOWN
    confidence = 0.2
    for markers, candidate, candidate_confidence in _STATUS_PATTERNS:
        if any(marker in normalized for marker in markers):
            status = candidate
            confidence = candidate_confidence
            break
    if (
        status == ImplementationStatus.UNKNOWN
        and official_source
        and event_category
        not in {
            PolicyEventCategory.OTHER,
            PolicyEventCategory.NOT_APPLICABLE,
        }
    ):
        status = ImplementationStatus.ANNOUNCED
        confidence = 0.55
    if event_category in {
        PolicyEventCategory.OTHER,
        PolicyEventCategory.NOT_APPLICABLE,
    }:
        status = ImplementationStatus.UNKNOWN
        confidence = 0.2
    flags: list[PolicyRiskFlag] = []
    if status in {
        ImplementationStatus.UNKNOWN,
        ImplementationStatus.RUMOR,
        ImplementationStatus.DRAFT,
        ImplementationStatus.CONSULTATION,
    }:
        flags.append(PolicyRiskFlag.IMPLEMENTATION_UNCERTAIN)
    if status == ImplementationStatus.TERMINATED:
        flags.append(PolicyRiskFlag.IMPLEMENTATION_TERMINATED)
    if status == ImplementationStatus.RETRACTED:
        flags.append(PolicyRiskFlag.POLICY_RETRACTED)
    implementation_time = (
        event_time
        if status
        in {
            ImplementationStatus.IMPLEMENTING,
            ImplementationStatus.EXECUTED,
        }
        else None
    )
    termination_time = (
        event_time
        if status
        in {
            ImplementationStatus.TERMINATED,
            ImplementationStatus.RETRACTED,
        }
        else None
    )
    return ImplementationResult(
        status=status,
        confidence=confidence,
        weight=implementation_weights()[status.value],
        implementation_time=implementation_time,
        termination_time=termination_time,
        risk_flags=tuple(flags),
    )


__all__ = ["ImplementationResult", "classify_implementation"]
