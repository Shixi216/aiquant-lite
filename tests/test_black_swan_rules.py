from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from scripts.analyze_stock import policy_factor, sentiment_factor
from trading.research.sentiment.black_swan_rules import FORCE_NEGATIVE, match_black_swan
from trading.research.sentiment.event_extractor import SentimentEventExtractor
from trading.research.sentiment.models import EventBundle, EventSourceRecord
from trading.research.sentiment.schemas import SentimentEventType
from router.schemas import RouterInvokeResponse


TZ = timezone(timedelta(hours=8))


def _official_bundle(text: str) -> EventBundle:
    now = datetime(2026, 7, 20, 20, 38, tzinfo=TZ)
    source = EventSourceRecord(
        record_id="official-1",
        event_time=now,
        fetched_at=now,
        source_name="上海证券交易所公告",
        source_url="https://www.sse.com.cn/notice/1",
        source_level="official",
        verified=True,
        content_hash="hash-1",
        payload={"title": "重要事项提示性公告", "content": text},
    )
    return EventBundle(
        event_cluster_id="cluster-1",
        canonical_title="重要事项提示性公告",
        cluster_event_type="announcement",
        event_time=now,
        data_cutoff=now,
        primary_source_id=source.record_id,
        source_count=1,
        source_records=[source],
        symbols=["688636"],
        sectors=[],
        dedup_method="single",
        dedup_version="v1",
        cluster_hash="cluster-hash",
    )


def test_order_restriction_forces_negative_without_model_call() -> None:
    result = __import__("asyncio").run(
        SentimentEventExtractor().extract(
            _official_bundle("从即日起三年，公司在特种领域获取订单受限。"),
            allow_model_calls=False,
        )
    )
    assert result.extraction.event_type == SentimentEventType.ORDER_RESTRICTION
    assert result.extraction.direction == -1
    assert result.extraction.intensity >= 0.8
    assert any(flag.value == FORCE_NEGATIVE for flag in result.risk_flags)


def test_order_restriction_overrides_neutral_model_output(monkeypatch) -> None:
    class Provider:
        async def invoke(self, **_):
            return RouterInvokeResponse(
                role="test",
                provider="fake",
                model="fake",
                content=json.dumps(
                    {
                        "event_type": "OTHER",
                        "direction": 0,
                        "intensity": 0.2,
                        "confidence": 0.7,
                        "impact_horizon": "UNKNOWN",
                        "fact_type": "OFFICIAL_FACT",
                        "affected_symbols": ["688636"],
                        "affected_sectors": [],
                        "summary": "模型判定中性",
                    },
                    ensure_ascii=False,
                ),
                latency_ms=1,
                usage={},
            )

    class Audit:
        def record_model_call(self, **_):
            return "audit-1"

    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "qwen",
    )
    result = __import__("asyncio").run(
        SentimentEventExtractor(
            provider_factory=lambda _: Provider(),
            audit_store=Audit(),
        ).extract(_official_bundle("从即日起三年，公司在特种领域获取订单受限。"), allow_model_calls=True)
    )
    assert result.used_model is True
    assert result.provider == "qwen"
    assert result.extraction.direction == -1
    assert result.extraction.intensity >= 0.8
    assert result.extraction.event_type == SentimentEventType.ORDER_RESTRICTION
    assert any(flag.value == FORCE_NEGATIVE for flag in result.risk_flags)


def test_one_forced_event_and_six_neutral_announcements_are_not_diluted() -> None:
    rows = [
        {
            "date": "07-20",
            "title": "重要事项提示性公告",
            "content": "从即日起三年，公司在特种领域获取订单受限。",
            "source_level": "official",
            "publisher": "巨潮资讯",
            "verified": True,
            "time": datetime(2026, 7, 20, 20, 38, tzinfo=TZ),
        }
    ]
    rows.extend(
        {
            "date": f"07-{21 + index:02d}",
            "title": f"第{index + 1}次董事会决议公告",
            "content": "",
            "source_level": "official",
            "publisher": "巨潮资讯",
            "verified": True,
            "time": datetime(2026, 7, 21 + index, tzinfo=TZ),
        }
        for index in range(6)
    )
    policy = policy_factor(rows)
    sentiment = sentiment_factor(rows, datetime(2026, 9, 2, tzinfo=TZ))
    assert policy["score"] <= -0.5
    forced = [event for event in sentiment["events"] if event["force_negative"]]
    assert forced and forced[0]["direction"] == -1
    assert forced[0]["event_type"] == "ORDER_RESTRICTION"


def test_positive_order_phrases_do_not_trigger() -> None:
    for text in ("订单增长", "订单饱满", "新签订单"):
        assert match_black_swan(
            text,
            source_level="official",
            source_name="巨潮资讯",
            verified=True,
        ) is None


def test_sentiment_regression_negative_and_normal_positive() -> None:
    now = datetime(2026, 8, 26, tzinfo=TZ)
    negative = sentiment_factor(
        [{"title": "净利下滑并裁员", "content": "", "publisher": "证券时报", "time": now}],
        now,
    )
    normal = sentiment_factor(
        [{"title": "公司经营正常并实现增长", "content": "", "publisher": "证券时报", "time": now}],
        now,
    )
    assert negative["score"] < 0
    assert normal["score"] >= 0
