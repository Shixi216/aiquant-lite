from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from trading.research.sentiment.v3.calculators import SnapshotBuildContext
from trading.research.sentiment.v3.reaction import calculate_event_reaction
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    Finality,
    MarketSentimentSnapshot,
    QualityFlag,
    RecordSource,
    SCHEMA_VERSION,
    SectorSentimentSnapshot,
    SectorType,
    SentimentSnapshotMeta,
    SnapshotType,
    StockTradingSentimentSnapshot,
)
from trading.research.sentiment.v3.text_models import (
    EventIntelligenceRecord,
    EventSession,
    EventType,
    ExtractionMethod,
    FactStatus,
    Novelty,
    ReactionState,
    TextDirection,
    TextExtractionOutput,
    TextHorizon,
    TextSourceQuality,
    TextType,
)
from trading.research.sentiment.v3.text_pipeline import (
    TextIntelligenceProcessor,
    analysis_cache_key,
    build_text_document,
    canonical_event_id,
    classify_event_session,
    document_content_hash,
    rule_fallback,
)

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 13)
SOURCE = datetime(2026, 8, 13, 15, 0, tzinfo=TZ)
AVAILABLE = datetime(2026, 8, 13, 16, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 8, 13, 16, 1, tzinfo=TZ)
NOW = datetime(2026, 8, 13, 16, 2, tzinfo=TZ)


def meta(
    kind: SnapshotType,
    *,
    day: date = DAY,
    scope: str = "000001.SZ",
    source_time: datetime = SOURCE,
    available: datetime = AVAILABLE,
) -> SentimentSnapshotMeta:
    return SentimentSnapshotMeta(
        snapshot_id=f"{kind.value}_{day}_{scope}",
        snapshot_type=kind,
        trade_date=day,
        symbol_scope=scope,
        source="TEST_LOCAL",
        source_timestamp=source_time,
        available_time=available,
        data_cutoff=max(available, CUTOFF),
        snapshot_time=max(available, NOW),
        finality=Finality.FINAL,
        data_version="v1",
        schema_version=SCHEMA_VERSION,
        algorithm_version="test-v1",
        content_hash="a" * 64,
        coverage_ratio=1,
        record_source=RecordSource.TEST,
        created_at=max(available, NOW),
    )


def document(
    title="公司拟回购5亿元",
    *,
    source="东方财富",
    published=SOURCE,
    body="公司拟回购5亿元股份",
    announcement=False,
    doc_id="d1",
):
    return build_text_document(
        document_id=doc_id,
        symbol="000001.SZ",
        title=title,
        body=body,
        source=source,
        published_time=published,
        ingested_time=AVAILABLE,
        is_announcement=announcement,
    )


def output(
    *,
    text_type=TextType.FACT_EVENT,
    event_type=EventType.BUYBACK,
    direction=TextDirection.POSITIVE,
    surprise=None,
    basis=None,
):
    return TextExtractionOutput(
        text_type=text_type,
        event_type=event_type,
        direction=direction,
        intensity=0.7,
        confidence=0.8,
        horizon=TextHorizon.SHORT,
        novelty=Novelty.NEW,
        surprise=surprise,
        surprise_basis=basis,
        available_time=SOURCE,
        source_quality=TextSourceQuality.MAINSTREAM_FINANCIAL_MEDIA,
        fact_status=FactStatus.REPORTED,
        evidence=["拟回购5亿元"],
    )


class FakeClient:
    provider = "qwen"
    model_name = "qwen-test"

    def __init__(self, *, fail=False, value=None):
        self.calls = 0
        self.fail = fail
        self.value = value or output()

    async def extract(self, doc):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated")
        return self.value.model_copy(
            update={"available_time": doc.available_time, "source_quality": doc.source_quality}
        ), {"latency_ms": 2.5, "model_name": "qwen-test"}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("公司拟回购股份", EventType.BUYBACK),
        ("股东计划减持", EventType.SHAREHOLDER_REDUCTION),
        ("公司收到处罚决定", EventType.PENALTY),
        ("涉及重大诉讼", EventType.LITIGATION),
        ("净利润同比增长", EventType.EARNINGS),
        ("普通信息", EventType.OTHER),
    ],
)
def test_rule_event_types(title, expected):
    assert rule_fallback(document(title=title, body=title)).event_type == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("公告公司拟回购", TextType.FACT_EVENT),
        ("机构认为公司估值偏低", TextType.OPINION),
        ("分析师上调预期", TextType.EXPECTATION_CHANGE),
        ("股价高开市场反应强烈", TextType.MARKET_REACTION),
        ("公司收到监管函", TextType.RISK),
        ("据传公司将并购", TextType.RUMOR),
        ("普通信息", TextType.OTHER),
    ],
)
def test_rule_text_types(title, expected):
    assert rule_fallback(document(title=title, body=title)).text_type == expected


def test_surprise_requires_explicit_basis():
    assert rule_fallback(document(title="业绩增长80%", body="净利润增长80%")).surprise is None
    value = rule_fallback(document(title="业绩超预期", body="净利润超过市场预期"))
    assert value.surprise == TextDirection.POSITIVE and value.surprise_basis
    with pytest.raises(ValueError):
        output(surprise=TextDirection.POSITIVE)


def test_content_hash_normalizes_whitespace_and_includes_source_time_body():
    a = document_content_hash(title="A  B", body="C\nD", source="X", published_time=SOURCE)
    b = document_content_hash(title="A B", body="C D", source="x", published_time=SOURCE)
    c = document_content_hash(title="A B", body="changed", source="x", published_time=SOURCE)
    assert a == b and a != c


def test_cache_key_versions_model_prompt_schema_algorithm():
    base = dict(content_hash="a" * 64, provider="qwen", model_name="m")
    assert analysis_cache_key(**base) != analysis_cache_key(**base, prompt_version="V2")
    assert analysis_cache_key(**base) != analysis_cache_key(
        content_hash="a" * 64, provider="qwen", model_name="m2"
    )
    assert analysis_cache_key(**base) != analysis_cache_key(**base, schema_version="2")


@pytest.mark.asyncio
async def test_exact_duplicate_and_second_run_make_zero_new_calls(tmp_path):
    repo = SentimentV3Repository(tmp_path / "s2d.duckdb")
    client = FakeClient()
    processor = TextIntelligenceProcessor(cache=repo, client=client, max_concurrency=2)
    first = await processor.process([document(), document(doc_id="d2")])
    second = await processor.process([document()])
    assert first[3].duplicate_suppressed == 1 and first[3].qwen_calls == 1
    assert second[3].cache_hits == 1 and second[3].qwen_calls == 0 and client.calls == 1


@pytest.mark.asyncio
async def test_qwen_failure_is_distinct_low_confidence_rule_fallback(tmp_path):
    repo = SentimentV3Repository(tmp_path / "fallback.duckdb")
    client = FakeClient(fail=True)
    analyses, events, _, stats = await TextIntelligenceProcessor(cache=repo, client=client).process(
        [document()]
    )
    assert analyses[0].extraction_method == ExtractionMethod.RULE_FALLBACK
    assert QualityFlag.LOW_CONFIDENCE in analyses[0].quality_flags
    assert stats.qwen_failures == stats.fallback_calls == 1 and events


def test_semantic_duplicate_same_event_different_source():
    a = document(title="公司拟回购5亿元", body="公司拟回购5亿元", source="东方财富")
    b = document(
        title="宣布最高5亿元回购计划",
        body="正式披露最高5亿元回购计划",
        source="CNInfo",
        announcement=True,
        doc_id="d2",
    )
    assert a.content_hash != b.content_hash
    assert canonical_event_id(a, output()) == canonical_event_id(b, output())


@pytest.mark.asyncio
async def test_official_and_media_merge_without_double_event_count(tmp_path):
    repo = SentimentV3Repository(tmp_path / "merge.duckdb")
    processor = TextIntelligenceProcessor(cache=repo, client=FakeClient())
    a = document(title="公司拟回购5亿元", body="公司拟回购5亿元", source="东方财富")
    b = document(
        title="宣布最高5亿元回购计划",
        body="正式披露最高5亿元回购计划",
        source="CNInfo",
        announcement=True,
        doc_id="d2",
    )
    _, events, _, _ = await processor.process([a, b])
    assert len(events) == 1 and events[0].official_confirmation_exists
    assert len(events[0].source_document_hashes) == 2


def test_date_only_announcement_is_not_midnight_and_is_flagged():
    value = build_text_document(
        document_id="d",
        symbol="000001.SZ",
        title="公告",
        body="",
        source="CNInfo",
        ingested_time=AVAILABLE,
        is_announcement=True,
        date_only=DAY,
    )
    assert value.available_time.hour == 16
    assert QualityFlag.AVAILABLE_TIME_INFERRED in value.quality_flags


def test_news_without_published_time_uses_ingestion_and_flags_unknown():
    value = build_text_document(
        document_id="d",
        symbol="000001.SZ",
        title="新闻",
        body="",
        source="东方财富",
        ingested_time=AVAILABLE,
    )
    assert value.published_time is None and value.available_time == AVAILABLE
    assert QualityFlag.PUBLISHED_TIME_UNKNOWN in value.quality_flags


def test_event_session_market_hours_and_weekend():
    assert classify_event_session(datetime(2026, 8, 13, 8, tzinfo=TZ)) == EventSession.PRE_MARKET
    assert classify_event_session(datetime(2026, 8, 13, 10, tzinfo=TZ)) == EventSession.IN_SESSION
    assert classify_event_session(datetime(2026, 8, 13, 18, tzinfo=TZ)) == EventSession.POST_MARKET
    assert (
        classify_event_session(datetime(2026, 8, 15, 10, tzinfo=TZ)) == EventSession.NON_TRADING_DAY
    )


def event(
    *,
    direction=TextDirection.POSITIVE,
    available=datetime(2026, 8, 13, 8, tzinfo=TZ),
    session=EventSession.PRE_MARKET,
):
    return EventIntelligenceRecord(
        event_id="e1",
        canonical_event_id="c1",
        symbol="000001.SZ",
        event_type=EventType.BUYBACK,
        direction=direction,
        intensity=0.7,
        confidence=0.8,
        horizon=TextHorizon.SHORT,
        novelty=Novelty.NEW,
        available_time=available,
        event_session=session,
        source_quality=TextSourceQuality.OFFICIAL_DISCLOSURE,
        fact_status=FactStatus.CONFIRMED,
        source_document_hashes=["a" * 64],
        official_confirmation_exists=True,
        extraction_method=ExtractionMethod.STRUCTURED_AI,
        provider="qwen",
        model_name="qwen-test",
        prompt_version="P1",
        schema_version=SCHEMA_VERSION,
        algorithm_version="a1",
        processed_at=NOW,
    )


def local_snapshots(
    *,
    stock_return=0.04,
    market_return=0.02,
    sector_return=0.07,
    market_present=True,
    sector_present=True,
    day=DAY,
):
    source = datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=15)
    available = source.replace(hour=16)
    market = (
        MarketSentimentSnapshot(
            meta=meta(
                SnapshotType.MARKET, day=day, scope="ALL_A", source_time=source, available=available
            )
        )
        if market_present
        else None
    )
    sector = SectorSentimentSnapshot(
        meta=meta(
            SnapshotType.SECTOR, day=day, scope="IND:A", source_time=source, available=available
        ),
        sector_id="IND:A",
        sector_name="A",
        sector_type=SectorType.INDUSTRY,
        universe_id="u",
        universe_version="uv",
        market_snapshot_id=market.meta.snapshot_id if market else None,
        membership_version="CURRENT",
        mean_return=sector_return,
        market_return=market_return,
    )
    if not sector_present:
        sector = None
    stock = StockTradingSentimentSnapshot(
        meta=meta(
            SnapshotType.STOCK, day=day, scope="000001.SZ", source_time=source, available=available
        ),
        symbol="000001.SZ",
        universe_id="u",
        universe_version="uv",
        industry_sector_id="IND:A",
        return_pct=stock_return,
        volume_change=0.3,
        turnover_change=0.2,
        close_location_value=0.25,
        open_gap_pct=0.05,
        high_to_close_drawdown=0.08,
        low_to_close_recovery=0.02,
        market_return=market_return,
        sector_return=sector_return,
        relative_market_return=stock_return - market_return,
        relative_sector_return=stock_return - sector_return,
        market_snapshot_id=market.meta.snapshot_id if market else None,
        sector_snapshot_id=sector.meta.snapshot_id if sector else None,
    )
    return market, sector, stock


def reaction_context(day=DAY):
    source = datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=15)
    available = source.replace(hour=16)
    return SnapshotBuildContext(
        trade_date=day,
        source="LOCAL_ONLY",
        source_timestamp=source,
        available_time=available,
        data_cutoff=available.replace(minute=1),
        snapshot_time=available.replace(minute=2),
        finality=Finality.FINAL,
        record_source=RecordSource.TEST,
        data_version="v1",
        symbol_scope="000001.SZ",
    )


@pytest.mark.parametrize(
    ("direction", "stock", "market", "sector", "expected"),
    [
        (TextDirection.POSITIVE, 0.08, 0.02, 0.03, ReactionState.CONFIRMED_POSITIVE),
        (TextDirection.POSITIVE, 0.01, 0.02, 0.03, ReactionState.POSITIVE_EVENT_REJECTED),
        (TextDirection.NEGATIVE, 0.08, 0.02, 0.03, ReactionState.NEGATIVE_EVENT_ABSORBED),
        (TextDirection.NEGATIVE, 0.01, 0.02, 0.03, ReactionState.CONFIRMED_NEGATIVE),
    ],
)
def test_event_reaction_taxonomy(direction, stock, market, sector, expected):
    market_s, sector_s, stock_s = local_snapshots(
        stock_return=stock, market_return=market, sector_return=sector
    )
    value = calculate_event_reaction(
        context=reaction_context(),
        event=event(direction=direction),
        stock=stock_s,
        market=market_s,
        sector=sector_s,
    )
    assert value.classification == expected
    assert value.stock_return == stock and value.relative_market_return == stock - market
    assert value.meta.formal_weight == 0


def test_missing_market_or_sector_keeps_relative_none_not_zero():
    market, sector, stock = local_snapshots(market_present=False, sector_present=False)
    value = calculate_event_reaction(
        context=reaction_context(), event=event(), stock=stock, market=market, sector=sector
    )
    assert value.relative_market_return is None and value.relative_sector_return is None
    assert value.classification == ReactionState.INSUFFICIENT_DATA
    assert QualityFlag.MISSING_MARKET_SNAPSHOT in value.meta.quality_flags


def test_post_market_event_rejects_same_day_eod_and_accepts_next_session():
    post = event(available=datetime(2026, 8, 13, 18, tzinfo=TZ), session=EventSession.POST_MARKET)
    market, sector, stock = local_snapshots()
    with pytest.raises(ValueError):
        calculate_event_reaction(
            context=reaction_context(), event=post, stock=stock, market=market, sector=sector
        )
    next_day = date(2026, 8, 14)
    market, sector, stock = local_snapshots(day=next_day)
    value = calculate_event_reaction(
        context=reaction_context(next_day), event=post, stock=stock, market=market, sector=sector
    )
    assert value.reaction_start.date() == next_day


def test_event_repository_asof_revision_cannot_see_future(tmp_path):
    repo = SentimentV3Repository(tmp_path / "events.duckdb")
    first = repo.save_event(event())
    changed = event().model_copy(
        update={
            "event_id": "e2",
            "source_document_hashes": ["a" * 64, "b" * 64],
            "available_time": datetime(2026, 8, 14, 8, tzinfo=TZ),
            "processed_at": datetime(2026, 8, 14, 9, tzinfo=TZ),
        }
    )
    second = repo.save_event(changed)
    old = repo.get_event_as_of(cutoff=datetime(2026, 8, 13, 23, tzinfo=TZ), canonical_event_id="c1")
    new = repo.get_event_as_of(cutoff=datetime(2026, 8, 14, 10, tzinfo=TZ), canonical_event_id="c1")
    assert first.revision == 1 and second.revision == 2
    assert old.event_id == "e1" and new.event_id == "e2"


def test_no_scores_or_actions_in_s2d_models():
    names = set(EventIntelligenceRecord.model_fields) | set(TextExtractionOutput.model_fields)
    assert not ({"score", "sentiment_score", "action", "formal_score", "veto"} & names)
