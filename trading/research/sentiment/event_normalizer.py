from __future__ import annotations

from trading.research.sentiment.models import EventBundle
from trading.research.sentiment.schemas import (
    FactType,
    ImpactHorizon,
    ModelSentimentExtraction,
    SentimentEventType,
)


_CLASSIFICATION_RULES: tuple[
    tuple[tuple[str, ...], SentimentEventType, int, float, ImpactHorizon],
    ...,
] = (
    (
        ("业绩预增", "扭亏", "大幅增长", "净利润增长", "盈利"),
        SentimentEventType.EARNINGS_POSITIVE,
        1,
        0.75,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("预亏", "亏损", "业绩预减", "大幅下降", "业绩警告"),
        SentimentEventType.EARNINGS_WARNING,
        -1,
        0.8,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("分红", "派息", "利润分配"),
        SentimentEventType.DIVIDEND,
        1,
        0.55,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("回购",),
        SentimentEventType.SHARE_REPURCHASE,
        1,
        0.6,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("减持",),
        SentimentEventType.SHAREHOLDER_REDUCTION,
        -1,
        0.65,
        ImpactHorizon.ONE_TO_FIVE_DAYS,
    ),
    (
        ("增持",),
        SentimentEventType.SHAREHOLDER_INCREASE,
        1,
        0.55,
        ImpactHorizon.ONE_TO_FIVE_DAYS,
    ),
    (
        ("中标", "重大合同", "签订合同"),
        SentimentEventType.CONTRACT_WIN,
        1,
        0.65,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("诉讼", "仲裁"),
        SentimentEventType.MAJOR_LITIGATION,
        -1,
        0.65,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("处罚", "罚款", "监管措施"),
        SentimentEventType.REGULATORY_PENALTY,
        -1,
        0.8,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("立案", "调查"),
        SentimentEventType.INVESTIGATION,
        -1,
        0.9,
        ImpactHorizon.LONG_TERM,
    ),
    (
        ("停牌",),
        SentimentEventType.TRADING_SUSPENSION,
        0,
        0.5,
        ImpactHorizon.UNKNOWN,
    ),
    (
        ("复牌",),
        SentimentEventType.TRADING_RESUMPTION,
        0,
        0.45,
        ImpactHorizon.ONE_DAY,
    ),
    (
        ("辞职", "离任", "高管变更", "董事变更"),
        SentimentEventType.MANAGEMENT_CHANGE,
        0,
        0.45,
        ImpactHorizon.ONE_TO_FOUR_WEEKS,
    ),
    (
        ("涨价", "降价", "价格调整"),
        SentimentEventType.PRODUCT_PRICE_CHANGE,
        0,
        0.55,
        ImpactHorizon.ONE_TO_FIVE_DAYS,
    ),
    (
        ("传闻", "据传", "市场消息"),
        SentimentEventType.MARKET_RUMOR,
        0,
        0.25,
        ImpactHorizon.UNKNOWN,
    ),
)


def normalize_event(bundle: EventBundle) -> ModelSentimentExtraction:
    primary = bundle.primary_source
    content = str(primary.payload.get("content") or "")
    text = f"{bundle.canonical_title} {content}"
    fact_type = (
        FactType.OFFICIAL_FACT
        if primary.source_level.casefold() == "official"
        else FactType.MEDIA_STATEMENT
    )
    for keywords, event_type, direction, intensity, horizon in (
        _CLASSIFICATION_RULES
    ):
        if any(keyword in text for keyword in keywords):
            return ModelSentimentExtraction(
                event_type=event_type,
                direction=direction,
                intensity=intensity,
                confidence=0.7 if fact_type == FactType.OFFICIAL_FACT else 0.5,
                impact_horizon=horizon,
                fact_type=fact_type,
                affected_symbols=bundle.symbols,
                affected_sectors=bundle.sectors,
                summary=bundle.canonical_title[:1000],
            )
    return ModelSentimentExtraction(
        event_type=(
            SentimentEventType.INDUSTRY_NEWS
            if bundle.sectors
            else SentimentEventType.OTHER
        ),
        direction=0,
        intensity=0.25,
        confidence=0.45 if fact_type == FactType.OFFICIAL_FACT else 0.3,
        impact_horizon=ImpactHorizon.UNKNOWN,
        fact_type=fact_type,
        affected_symbols=bundle.symbols,
        affected_sectors=bundle.sectors,
        summary=bundle.canonical_title[:1000],
    )


__all__ = ["normalize_event"]
