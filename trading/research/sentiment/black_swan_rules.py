"""官方/权威来源黑天鹅事件的本地确定性规则。

模型只能补充结构化信息；命中本表时，本地规则拥有最终方向裁决权。
词表面向全市场，禁止按股票代码或公司名称特判。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from trading.research.sentiment.schemas import SentimentEventType


FORCE_NEGATIVE = "FORCE_NEGATIVE"
MIN_FORCE_INTENSITY = 0.8

# 仅官方公告、监管机构或权威财经媒体可触发硬规则。
TRUSTED_SOURCE_LEVELS = {
    "official",
    "official_announcement",
    "official_policy",
    "policy",
    "regulatory",
    "authoritative_media",
}
TRUSTED_SOURCE_NAMES = (
    "巨潮",
    "交易所",
    "上交所",
    "深交所",
    "北交所",
    "证监会",
    "证券时报",
    "上海证券报",
    "中国证券报",
    "证券日报",
)


@dataclass(frozen=True)
class BlackSwanMatch:
    keyword: str
    event_type: SentimentEventType
    intensity: float


# 顺序即优先级；更具体的事件必须排在泛化词之前。
BLACK_SWAN_KEYWORDS: tuple[
    tuple[tuple[str, ...], SentimentEventType, float], ...
] = (
    (
        ("获取订单受限", "订单受限", "订单限制", "限制获取订单"),
        SentimentEventType.ORDER_RESTRICTION,
        1.0,
    ),
    (
        ("监管工作函", "监管函"),
        SentimentEventType.REGULATORY_LETTER,
        0.9,
    ),
    (
        ("立案调查", "立案"),
        SentimentEventType.INVESTIGATION,
        0.95,
    ),
    (
        ("停产整顿", "停产", "停业"),
        SentimentEventType.OTHER,
        0.9,
    ),
    (
        ("失去资格", "取消资格", "被列入", "被暂停"),
        SentimentEventType.OTHER,
        0.85,
    ),
    (
        ("主要客户流失", "核心业务受限", "主营业务受限", "重大合同终止"),
        SentimentEventType.OTHER,
        0.9,
    ),
    (
        ("破产", "重整申请", "债务逾期"),
        SentimentEventType.OTHER,
        0.95,
    ),
)

_EXECUTIVE_EVENT = re.compile(
    r"(?:实控人|实际控制人|董事长|高管).{0,12}(?:调查|拘留|逮捕|起诉)"
)


def is_trusted_source(
    *,
    source_level: str = "",
    source_name: str = "",
    verified: bool = False,
) -> bool:
    level = str(source_level or "").casefold()
    name = str(source_name or "")
    return (
        level in TRUSTED_SOURCE_LEVELS
        or any(marker in name for marker in TRUSTED_SOURCE_NAMES)
        or (verified and level != "social")
    )


def match_black_swan(
    text: str,
    *,
    source_level: str = "",
    source_name: str = "",
    verified: bool = False,
) -> BlackSwanMatch | None:
    """可信来源命中黑天鹅词时返回强制负面分类，否则返回 None。"""
    if not is_trusted_source(
        source_level=source_level,
        source_name=source_name,
        verified=verified,
    ):
        return None
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return None
    for keywords, event_type, intensity in BLACK_SWAN_KEYWORDS:
        for keyword in keywords:
            if keyword in normalized:
                return BlackSwanMatch(keyword, event_type, max(MIN_FORCE_INTENSITY, intensity))
    executive = _EXECUTIVE_EVENT.search(normalized)
    if executive:
        return BlackSwanMatch(
            executive.group(0),
            SentimentEventType.INVESTIGATION,
            0.95,
        )
    return None


__all__ = [
    "BLACK_SWAN_KEYWORDS",
    "BlackSwanMatch",
    "FORCE_NEGATIVE",
    "MIN_FORCE_INTENSITY",
    "is_trusted_source",
    "match_black_swan",
]
