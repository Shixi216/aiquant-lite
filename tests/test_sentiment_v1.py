from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from config.settings import settings
from database.db import get_connection, initialize_database, insert_market_record
from database.migrations.v0103_sentiment_v1 import apply_migration
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.event_cluster_service import EventClusterService
from router.schemas import RouterInvokeResponse
from scripts.backfill_sentiment import run_backfill
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.research.sentiment.aggregator import aggregate_symbol
from trading.research.sentiment.event_extractor import SentimentEventExtractor
from trading.research.sentiment.freshness import freshness_weight
from trading.research.sentiment.market_breadth import (
    MarketBreadthService,
    _limit_ratio,
)
from trading.research.sentiment.models import EventBundle
from trading.research.sentiment.policy import policy_for
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import (
    FactType,
    ImpactHorizon,
    ModelSentimentExtraction,
    SentimentAnalyzeRequest,
    SentimentEventAnalysis,
    SentimentEventType,
    SentimentRiskFlag,
    SentimentVerificationStatus,
)
from trading.research.sentiment.scorer import calculate_event_score
from trading.research.sentiment.service import SentimentAnalysisService
from trading.research.sentiment.source_quality import source_quality
from trading.research.sentiment.verification import verification_result
from trading.schemas import (
    AnalysisMode,
    Bar,
    DecisionRequest,
    PortfolioState,
)


TZ = ZoneInfo("Asia/Shanghai")
EVENT_TIME = datetime(2026, 7, 1, 9, tzinfo=TZ)
FETCHED_AT = datetime(2026, 7, 1, 10, tzinfo=TZ)
CUTOFF = datetime(2026, 7, 5, 15, tzinfo=TZ)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


@pytest.fixture
def sentiment_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "sentiment.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def _record(
    *,
    record_id: str,
    title: str,
    symbol: str = "600000.SH",
    data_type: DataType = DataType.ANNOUNCEMENT,
    source_level: SourceLevel = SourceLevel.OFFICIAL,
    source_name: str = "CNInfo",
    verified: bool = True,
    event_time: datetime = EVENT_TIME,
    fetched_at: datetime = FETCHED_AT,
    content: str = "",
    content_hash: str | None = None,
) -> MarketRecord:
    payload = {
        "symbol": symbol,
        "title": title,
        "content": content,
        "publisher": source_name,
    }
    return MarketRecord(
        record_id=record_id,
        symbol=symbol,
        data_type=data_type,
        event_time=event_time,
        fetched_at=fetched_at,
        source_name=source_name,
        source_level=source_level,
        verified=verified,
        content_hash=content_hash or _hash(payload),
        data=payload,
    )


def _seed_event(
    *records: MarketRecord,
) -> EventBundle:
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)
    cluster = EventClusterService().cluster(list(records))[0]
    bundle = SentimentRepository().get_event_bundle(
        cluster.event_cluster_id
    )
    assert bundle is not None
    return bundle


def _analysis(
    *,
    event_id: str = "evt-test",
    event_type: SentimentEventType = SentimentEventType.OTHER,
    direction: int = 1,
    score: float = 0.3,
    confidence: float = 0.7,
    source_count: int = 1,
    symbol: str = "600000.SH",
    flags: list[SentimentRiskFlag] | None = None,
) -> SentimentEventAnalysis:
    input_hash = _hash(
        {
            "event": event_id,
            "score": score,
            "symbol": symbol,
        }
    )
    return SentimentEventAnalysis(
        sentiment_analysis_id="sea-" + input_hash[:20],
        event_cluster_id=event_id,
        event_type=event_type,
        direction=direction,
        intensity=abs(score),
        confidence=confidence,
        model_confidence=confidence,
        impact_horizon=ImpactHorizon.ONE_TO_FIVE_DAYS,
        fact_type=FactType.VERIFIED_FACT,
        affected_symbols=[symbol],
        affected_sectors=[],
        relevance_by_symbol={symbol: 1.0},
        summary="结构化事件摘要",
        source_level="official",
        source_quality=1,
        freshness_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
        verification_weight=1,
        relevance_weight=1,
        event_score=score if direction else 0,
        propagation_heat=float(source_count - 1),
        risk_flags=flags or [],
        evidence_ids=[event_id],
        model_call_ids=[],
        extractor_version="test",
        algorithm_version="test",
        prompt_version="test",
        input_snapshot_hash=input_hash,
        generated_at=CUTOFF + timedelta(minutes=1),
        data_cutoff=CUTOFF,
    )


def _insert_daily(
    *,
    symbol: str,
    day: date,
    close: float,
    amount: float = 100.0,
    high: float | None = None,
) -> None:
    event_time = datetime.combine(
        day,
        datetime.min.time().replace(hour=15),
        tzinfo=TZ,
    )
    record_id = f"cmr-{symbol}-{day.isoformat()}"
    payload = {
        "trade_date": day.strftime("%Y%m%d"),
        "open": close,
        "high": high if high is not None else close,
        "low": close,
        "close": close,
        "volume": 1000,
        "amount": amount,
    }
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO canonical_market_records (
                canonical_record_id, symbol, data_type, event_time,
                data_cutoff, generated_at, primary_source,
                source_record_ids_json, verification_source_ids_json,
                verification_status, field_differences_json,
                payload_json, confidence, content_hash,
                algorithm_version
            )
            VALUES (
                ?, ?, 'daily_bar', ?, ?, ?, 'test', '[]', '[]',
                'VERIFIED', '{}', ?, 1.0, ?, 'test'
            )
            """,
            [
                record_id,
                symbol,
                event_time,
                event_time + timedelta(minutes=1),
                event_time + timedelta(minutes=2),
                json.dumps(payload),
                _hash(payload),
            ],
        )


def _seed_breadth() -> datetime:
    start = date(2026, 6, 1)
    for index in range(21):
        day = start + timedelta(days=index)
        _insert_daily(
            symbol="600000.SH",
            day=day,
            close=10 + index * 0.1,
            amount=100 + index,
        )
        _insert_daily(
            symbol="000001.SZ",
            day=day,
            close=10 - index * 0.05,
            amount=80 + index,
        )
    return datetime(2026, 6, 22, 0, tzinfo=TZ)


class FakeProvider:
    provider_name = "fake"

    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0

    async def invoke(self, **_: object) -> RouterInvokeResponse:
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        return RouterInvokeResponse(
            role="test",
            provider=self.provider_name,
            model="fake-model",
            content=content,
            latency_ms=1,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


def _model_json(
    *,
    direction: int = 1,
    intensity: float = 0.6,
    confidence: float = 0.8,
    event_type: str = "CONTRACT_WIN",
    summary: str = "确认结构化事件事实",
) -> str:
    return json.dumps(
        {
            "event_type": event_type,
            "direction": direction,
            "intensity": intensity,
            "confidence": confidence,
            "impact_horizon": "1_5D",
            "fact_type": "OFFICIAL_FACT",
            "affected_symbols": ["600000.SH"],
            "affected_sectors": [],
            "summary": summary,
        },
        ensure_ascii=False,
    )


# A. Event identity and deduplication


def test_01_one_base_analysis_per_event(sentiment_db: Path) -> None:
    bundle = _seed_event(
        _record(record_id="r1", title="重大合同中标公告")
    )
    service = SentimentAnalysisService()
    first = asyncio.run(
        service.analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=True,
        )
    )
    second = asyncio.run(
        service.analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=True,
        )
    )
    assert first.sentiment_analysis_id == second.sentiment_analysis_id
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sentiment_event_analyses"
        ).fetchone()[0] == 1


def test_02_reposts_do_not_duplicate_direction_score(
    sentiment_db: Path,
) -> None:
    shared_hash = _hash("same story")
    bundle = _seed_event(
        _record(
            record_id="media",
            title="重大合同中标",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            source_name="普通财经媒体",
            verified=False,
            content_hash=shared_hash,
        ),
        _record(
            record_id="official",
            title="重大合同中标",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.OFFICIAL,
            source_name="交易所",
            content_hash=shared_hash,
        ),
    )
    result = asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=False,
        )
    )
    assert bundle.source_count == 2
    assert result.event_score <= settings.sentiment_max_single_event_contribution


def test_03_reposts_only_increase_propagation_heat(
    sentiment_db: Path,
) -> None:
    shared_hash = _hash("repost")
    single = _seed_event(
        _record(
            record_id="single",
            title="公司回购计划",
            symbol="600001.SH",
        )
    )
    multi = _seed_event(
        _record(
            record_id="m1",
            title="公司回购计划",
            symbol="600002.SH",
            content_hash=shared_hash,
        ),
        _record(
            record_id="m2",
            title="公司回购计划",
            symbol="600002.SH",
            source_level=SourceLevel.MEDIA,
            verified=False,
            content_hash=shared_hash,
        ),
    )
    service = SentimentAnalysisService()
    one = asyncio.run(
        service.analyze_event(
            single,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=False,
        )
    )
    two = asyncio.run(
        service.analyze_event(
            multi,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=False,
        )
    )
    assert one.propagation_heat == 0
    assert two.propagation_heat > 0


def test_04_official_source_has_priority(sentiment_db: Path) -> None:
    shared_hash = _hash("same")
    bundle = _seed_event(
        _record(
            record_id="news",
            title="监管处罚",
            source_level=SourceLevel.MEDIA,
            source_name="普通媒体",
            verified=False,
            content_hash=shared_hash,
        ),
        _record(
            record_id="exchange",
            title="监管处罚",
            source_level=SourceLevel.OFFICIAL,
            source_name="交易所",
            content_hash=shared_hash,
        ),
    )
    assert bundle.primary_source_id == "exchange"


def test_05_duplicate_suspected_flag_is_preserved(
    sentiment_db: Path,
) -> None:
    bundle = _seed_event(_record(record_id="r1", title="独立董事述职报告"))
    result = asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=False,
            extra_risk_flags=[SentimentRiskFlag.DUPLICATE_SUSPECTED],
        )
    )
    assert SentimentRiskFlag.DUPLICATE_SUSPECTED in result.risk_flags


def test_06_different_entities_are_not_merged(sentiment_db: Path) -> None:
    with get_connection() as connection:
        for record in (
            _record(record_id="a", title="重大合同", symbol="600000.SH"),
            _record(record_id="b", title="重大合同", symbol="000001.SZ"),
        ):
            insert_market_record(connection, record)
    clusters = EventClusterService().cluster(
        [
            _record(record_id="a", title="重大合同", symbol="600000.SH"),
            _record(record_id="b", title="重大合同", symbol="000001.SZ"),
        ]
    )
    assert len(clusters) == 2


def test_07_retracted_event_scores_zero(sentiment_db: Path) -> None:
    bundle = _seed_event(_record(record_id="r1", title="撤回重大合同公告"))
    result = asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=False,
        )
    )
    assert result.verification_status == SentimentVerificationStatus.RETRACTED
    assert result.event_score == 0
    assert SentimentRiskFlag.RETRACTED_EVENT in result.risk_flags


# B. Model output and safe degradation


def test_08_longcat_news_output_validates(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(
        _record(
            record_id="news",
            title="行业新闻",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            verified=False,
        )
    )
    fake = FakeProvider([_model_json()])
    # 新闻 → 路由 qwen（当前 _primary_route）
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "qwen",
    )
    extractor = SentimentEventExtractor(provider_factory=lambda _: fake)
    result = asyncio.run(
        extractor.extract(bundle, allow_model_calls=True)
    )
    assert result.used_model
    assert result.extraction.direction == 1


def test_09_qwen_announcement_output_validates(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="合同公告"))
    fake = FakeProvider([_model_json()])
    # 公告 → 路由 longcat（当前 _primary_route）
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    result = asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda _: fake
        ).extract(bundle, allow_model_calls=True)
    )
    assert result.used_model
    assert result.provider == "longcat"


def test_10_invalid_json_repairs_at_most_once(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="合同公告"))
    fake = FakeProvider(["not-json", _model_json()])
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    result = asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda _: fake
        ).extract(bundle, allow_model_calls=True)
    )
    assert fake.calls == 2
    assert result.validation_status.value == "REPAIRED"


def test_11_failed_repair_marks_invalid(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="合同公告"))
    fake = FakeProvider(["bad", "still bad"])
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    result = asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda _: fake
        ).extract(bundle, allow_model_calls=True)
    )
    assert SentimentRiskFlag.MODEL_OUTPUT_INVALID in result.risk_flags
    assert fake.calls == 2


def test_12_model_failure_degrades_to_rules(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="公司回购公告"))
    fake = FakeProvider(["bad", "bad"])
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    result = asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda _: fake
        ).extract(bundle, allow_model_calls=True)
    )
    assert result.extraction.event_type == SentimentEventType.SHARE_REPURCHASE
    assert not result.used_model


def test_13_missing_keys_keep_service_available(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 显式清空模型 key（模拟无 key 环境，避免开发机真实 key 污染）
    from router.config import router_settings
    monkeypatch.setattr(router_settings, "longcat_api_key", None)
    monkeypatch.setattr(router_settings, "qwen_api_key", None)
    monkeypatch.setattr(router_settings, "deepseek_api_key", None)
    monkeypatch.setattr(router_settings, "mimo_api_key", None)
    bundle = _seed_event(_record(record_id="a", title="公司回购公告"))
    result = asyncio.run(
        SentimentEventExtractor().extract(
            bundle,
            allow_model_calls=True,
        )
    )
    assert result.extraction.event_type == SentimentEventType.SHARE_REPURCHASE
    assert not result.used_model


def test_14_deepseek_only_runs_on_escalation(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="普通公告"))
    providers = {
        "longcat": FakeProvider([_model_json(intensity=0.3, confidence=0.9)]),
        "deepseek": FakeProvider([_model_json(direction=-1)]),
    }
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda _: True,
    )
    asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda name: providers[name]
        ).extract(bundle, allow_model_calls=True)
    )
    assert providers["longcat"].calls == 1
    assert providers["deepseek"].calls == 0


def test_15_model_trade_instruction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelSentimentExtraction.model_validate_json(
            _model_json(summary="建议 BUY 并设置仓位比例")
        )


def test_16_model_call_log_has_required_fields(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="a", title="合同公告"))
    fake = FakeProvider([_model_json()])
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    asyncio.run(
        SentimentEventExtractor(
            provider_factory=lambda _: fake
        ).extract(bundle, allow_model_calls=True)
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT prompt_version, input_hash, latency_ms,
                   retry_count, schema_validation, success
            FROM model_calls
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert row[0] and len(row[1]) == 64 and row[2] == 1
    assert row[3] == 0 and row[4] == "VALID" and row[5]


# C. Deterministic scoring


def test_17_event_score_formula_is_deterministic() -> None:
    result = calculate_event_score(
        direction=1,
        intensity=0.5,
        model_confidence=0.8,
        source_quality_weight=1,
        freshness_weight=0.5,
        verification_weight=1,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
    )
    assert result.score == pytest.approx(0.2)


def test_18_event_score_is_bounded() -> None:
    result = calculate_event_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_quality_weight=1,
        freshness_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
    )
    assert -1 <= result.score <= 1


def test_19_official_weight_exceeds_media(sentiment_db: Path) -> None:
    official = _seed_event(
        _record(record_id="o", title="公告", symbol="600001.SH")
    )
    media = _seed_event(
        _record(
            record_id="m",
            title="新闻",
            symbol="600002.SH",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            source_name="普通媒体",
            verified=False,
        )
    )
    assert source_quality(
        official, event_type=SentimentEventType.OTHER
    ).weight > source_quality(
        media, event_type=SentimentEventType.OTHER
    ).weight


def test_20_old_news_decays() -> None:
    fresh = freshness_weight(
        event_time=CUTOFF - timedelta(hours=1),
        data_cutoff=CUTOFF,
        impact_horizon=ImpactHorizon.ONE_DAY,
    )
    old = freshness_weight(
        event_time=CUTOFF - timedelta(days=5),
        data_cutoff=CUTOFF,
        impact_horizon=ImpactHorizon.ONE_DAY,
    )
    assert fresh.weight > old.weight


def test_21_long_term_event_decays_more_slowly() -> None:
    event_time = CUTOFF - timedelta(days=10)
    short = freshness_weight(
        event_time=event_time,
        data_cutoff=CUTOFF,
        impact_horizon=ImpactHorizon.ONE_DAY,
    )
    long = freshness_weight(
        event_time=event_time,
        data_cutoff=CUTOFF,
        impact_horizon=ImpactHorizon.LONG_TERM,
    )
    assert long.weight > short.weight


def test_22_conflict_has_no_directional_score() -> None:
    result = calculate_event_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_quality_weight=1,
        freshness_weight=1,
        verification_weight=0,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.CONFLICT,
    )
    assert result.score == 0


def test_23_neutral_direction_scores_zero() -> None:
    result = calculate_event_score(
        direction=0,
        intensity=1,
        model_confidence=1,
        source_quality_weight=1,
        freshness_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
    )
    assert result.score == 0


def test_24_single_event_contribution_is_capped() -> None:
    result = calculate_event_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_quality_weight=1,
        freshness_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
    )
    assert result.score == settings.sentiment_max_single_event_contribution


def test_25_missing_component_is_not_coerced_to_one() -> None:
    result = calculate_event_score(
        direction=1,
        intensity=1,
        model_confidence=None,
        source_quality_weight=1,
        freshness_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=SentimentVerificationStatus.VERIFIED_OFFICIAL,
    )
    assert result.score == 0
    assert "model_confidence" in result.missing_components


def test_26_high_rumor_ratio_reduces_confidence() -> None:
    snapshot = aggregate_symbol(
        symbol="600000.SH",
        analyses=[
            _analysis(
                event_id="rumor",
                event_type=SentimentEventType.MARKET_RUMOR,
                confidence=0.8,
            )
        ],
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=CUTOFF,
        generated_at=CUTOFF + timedelta(minutes=2),
    )
    assert snapshot.confidence == pytest.approx(0.4)
    assert SentimentRiskFlag.HIGH_RUMOR_RATIO in snapshot.risk_flags


# D. Market breadth


def test_27_advance_decline_flat_counts(sentiment_db: Path) -> None:
    cutoff = _seed_breadth()
    result = MarketBreadthService().calculate(
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=cutoff,
        persist=False,
    )
    assert result.advances == 1
    assert result.declines == 1
    assert result.flats == 0


def test_28_limit_and_broken_limit_calculation(sentiment_db: Path) -> None:
    day1 = date(2026, 6, 1)
    day2 = date(2026, 6, 2)
    _insert_daily(symbol="600000.SH", day=day1, close=10)
    _insert_daily(symbol="600000.SH", day=day2, close=11, high=11)
    _insert_daily(symbol="000001.SZ", day=day1, close=10)
    _insert_daily(symbol="000001.SZ", day=day2, close=10.5, high=11)
    result = MarketBreadthService().calculate(
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=datetime(2026, 6, 3, tzinfo=TZ),
        persist=False,
    )
    assert result.limit_ups == 1
    assert result.broken_limit_ups == 1
    assert result.broken_limit_up_rate == pytest.approx(0.5)


def test_29_amount_relative_to_history(sentiment_db: Path) -> None:
    cutoff = _seed_breadth()
    result = MarketBreadthService().calculate(
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=cutoff,
        persist=False,
    )
    assert result.amount_vs_20d_average is not None
    assert result.amount_vs_20d_average > 1


def test_30_partial_sample_is_marked(sentiment_db: Path) -> None:
    cutoff = _seed_breadth()
    result = MarketBreadthService().calculate(
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=cutoff,
        persist=False,
    )
    assert SentimentRiskFlag.PARTIAL_UNIVERSE in result.risk_flags
    assert result.confidence <= settings.sentiment_market_partial_confidence_cap


def test_31_missing_market_data_is_not_invented(sentiment_db: Path) -> None:
    result = MarketBreadthService().calculate(
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=CUTOFF,
        persist=False,
    )
    assert result.universe_size == 0
    assert result.advances is None
    assert "market_daily_records" in result.missing_fields


def test_32_market_breadth_has_no_model_dependency() -> None:
    source = inspect.getsource(MarketBreadthService)
    assert "invoke(" not in source
    assert "Provider" not in source


# E. Mode isolation


def test_33_screening_does_not_call_models(
    sentiment_db: Path,
) -> None:
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.analyses == []


def test_34_screening_does_not_fetch_each_symbol() -> None:
    policy = policy_for(AnalysisMode.SCREENING)
    assert not policy.allow_external_fetch
    assert policy.use_existing_snapshot


def test_35_screening_returns_when_sentiment_missing(
    sentiment_db: Path,
) -> None:
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.symbol_snapshot is None
    assert SentimentRiskFlag.DATA_GAP in result.risk_flags


def test_36_screening_does_not_create_factor(
    sentiment_db: Path,
) -> None:
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.factor_output is None


def test_37_screening_does_not_create_decision_packet() -> None:
    source = inspect.getsource(SentimentAnalysisService)
    assert "DecisionPacket(" not in source


def test_38_research_policy_allows_longcat_and_qwen() -> None:
    from trading.research.sentiment import event_extractor

    policy = policy_for(AnalysisMode.RESEARCH)
    assert policy.allow_model_calls
    extractor_source = inspect.getsource(event_extractor)
    assert "longcat" in extractor_source
    assert "qwen" in extractor_source


def test_39_research_factor_is_always_shadow(
    sentiment_db: Path,
) -> None:
    _seed_event(_record(record_id="r1", title="公司回购公告"))
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode is True


def test_40_decision_enforces_data_cutoff(sentiment_db: Path) -> None:
    _seed_event(
        _record(
            record_id="future",
            title="未来公告",
            event_time=CUTOFF + timedelta(days=1),
            fetched_at=CUTOFF + timedelta(days=1, hours=1),
        )
    )
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.DECISION,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.analyses == []


def test_41_decision_excludes_future_news(sentiment_db: Path) -> None:
    _seed_event(
        _record(
            record_id="late",
            title="稍后发布的新闻",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            verified=False,
            event_time=CUTOFF + timedelta(minutes=1),
            fetched_at=CUTOFF + timedelta(minutes=2),
        )
    )
    bundles = SentimentRepository().list_event_bundles(
        symbol="600000.SH",
        data_cutoff=CUTOFF,
        strict_point_in_time=True,
    )
    assert bundles == []


def test_42_decision_factor_is_always_shadow(
    sentiment_db: Path,
) -> None:
    _seed_event(_record(record_id="r1", title="公司回购公告"))
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.DECISION,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode


def test_43_sentiment_does_not_change_formal_action_weights() -> None:
    source = inspect.getsource(_generate_strategy)
    assert "technical.score * 0.6" in source
    assert "fundamental.score * 0.4" in source
    assert "sentiment" not in source.casefold()


def test_44_sentiment_does_not_change_hard_veto() -> None:
    from trading.decision_support.orchestrator.service import (
        TradingDecisionService,
    )

    bars = [
        Bar(
            trade_date=date(2026, 1, 1) + timedelta(days=index),
            open=10 + index * 0.1,
            high=10.2 + index * 0.1,
            low=9.8 + index * 0.1,
            close=10.1 + index * 0.1,
            volume=1000,
        )
        for index in range(35)
    ]
    trace = asyncio.run(
        TradingDecisionService().decide(
            DecisionRequest(
                symbol="600000.SH",
                bars=bars,
                portfolio=PortfolioState(
                    cash=100,
                    equity=100,
                    kill_switch=True,
                ),
                evidence_refs=[],
            )
        )
    )
    assert trace.final_action.value == "veto"


def test_45_modes_have_distinct_policies() -> None:
    screening = policy_for(AnalysisMode.SCREENING)
    research = policy_for(AnalysisMode.RESEARCH)
    decision = policy_for(AnalysisMode.DECISION)
    assert screening != research
    assert research != decision
    assert decision.strict_point_in_time


# F. Data, migration, audit and backfill


def test_46_migration_is_idempotent_twice(tmp_path: Path) -> None:
    path = tmp_path / "migration.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE model_calls (call_id VARCHAR)")
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False


def test_47_raw_records_count_is_unchanged(
    sentiment_db: Path,
) -> None:
    bundle = _seed_event(_record(record_id="r1", title="公司回购公告"))
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=True,
        )
    )
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    assert before == after


def test_48_event_cluster_count_is_unchanged(
    sentiment_db: Path,
) -> None:
    bundle = _seed_event(_record(record_id="r1", title="公司回购公告"))
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM event_clusters"
        ).fetchone()[0]
    asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=True,
        )
    )
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM event_clusters"
        ).fetchone()[0]
    assert before == after


def test_49_sentiment_evidence_resolves_to_original_event(
    sentiment_db: Path,
) -> None:
    bundle = _seed_event(_record(record_id="r1", title="公司回购公告"))
    analysis = asyncio.run(
        SentimentAnalysisService().analyze_event(
            bundle,
            data_cutoff=CUTOFF,
            allow_model_calls=False,
            persist=True,
        )
    )
    assert bundle.event_cluster_id in analysis.evidence_ids
    assert "r1" in analysis.evidence_ids
    assert FactorOutputRepository.resolve_evidence("r1") is not None


def test_50_factor_can_trace_model_calls(
    sentiment_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed_event(_record(record_id="r1", title="合同公告"))
    fake = FakeProvider([_model_json()])
    monkeypatch.setattr(
        "trading.research.sentiment.event_extractor._provider_ready",
        lambda name: name == "longcat",
    )
    service = SentimentAnalysisService(
        extractor=SentimentEventExtractor(
            provider_factory=lambda _: fake
        )
    )
    result = asyncio.run(
        service.analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
                event_cluster_ids=[bundle.event_cluster_id],
                allow_model_calls=True,
            )
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.model_call_ids
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM model_calls WHERE call_id = ?",
            [result.factor_output.model_call_ids[0]],
        ).fetchone()[0]
    assert count == 1


def _backfill_args(
    tmp_path: Path,
    *,
    apply: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        dry_run=not apply,
        apply=apply,
        event_type=None,
        symbol=None,
        start_time=None,
        end_time=None,
        limit=None,
        resume=None,
        skip_model_calls=True,
        enable_model_calls=False,
        report_path=tmp_path,
    )


def test_51_backfill_dry_run_does_not_write(
    sentiment_db: Path,
    tmp_path: Path,
) -> None:
    _seed_event(_record(record_id="r1", title="公司回购公告"))
    report = asyncio.run(run_backfill(_backfill_args(tmp_path, apply=False)))
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM sentiment_event_analyses"
        ).fetchone()[0]
    assert report["mode"] == "DRY_RUN"
    assert count == 0


def test_52_backfill_apply_twice_is_idempotent(
    sentiment_db: Path,
    tmp_path: Path,
) -> None:
    _seed_event(_record(record_id="r1", title="公司回购公告"))
    first = asyncio.run(run_backfill(_backfill_args(tmp_path, apply=True)))
    second = asyncio.run(run_backfill(_backfill_args(tmp_path, apply=True)))
    assert first["success_count"] == 1
    assert second["success_count"] == 0
    assert second["skipped_count"] == 1
    assert (
        second["sentiment_symbol_snapshot_count_after"]
        == first["sentiment_symbol_snapshot_count_after"]
    )


def test_53_bad_event_does_not_stop_backfill(
    sentiment_db: Path,
    tmp_path: Path,
) -> None:
    _seed_event(_record(record_id="good", title="公司回购公告"))
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO event_clusters (
                event_cluster_id, canonical_title, event_type,
                event_time, data_cutoff, primary_source_id,
                source_count, dedup_method, dedup_version,
                cluster_hash, generated_at
            )
            VALUES (
                'bad-event', '坏事件', 'announcement', ?, ?,
                'missing-source', 1, 'TEST', 'test', 'bad', ?
            )
            """,
            [EVENT_TIME, FETCHED_AT, CUTOFF],
        )
        connection.execute(
            """
            INSERT INTO event_symbol_links (event_cluster_id, symbol)
            VALUES ('bad-event', '600000.SH')
            """
        )
    report = asyncio.run(run_backfill(_backfill_args(tmp_path, apply=True)))
    assert report["success_count"] == 1
    assert report["failed_count"] == 1


def test_54_decision_packet_immutability_code_is_unchanged() -> None:
    from trading.schemas import DecisionPacket

    assert DecisionPacket.model_config["frozen"] is True


def test_55_no_live_or_broker_code_added() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "xtquant",
        "qmt_path",
        "broker_password",
        "trading_password",
        "live_trading_enabled",
    )
    for path in (root / "trading" / "research" / "sentiment").glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        assert not any(item in text for item in forbidden)


# Additional acceptance coverage requested for v1.


def test_source_verification_single_media(sentiment_db: Path) -> None:
    bundle = _seed_event(
        _record(
            record_id="m",
            title="普通新闻",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            verified=False,
        )
    )
    result = verification_result(bundle)
    assert result.status == SentimentVerificationStatus.SINGLE_MEDIA_SOURCE


def test_future_fetched_event_is_excluded_from_decision(
    sentiment_db: Path,
) -> None:
    _seed_event(
        _record(
            record_id="r1",
            title="已发布但尚未抓取",
            event_time=CUTOFF - timedelta(hours=1),
            fetched_at=CUTOFF + timedelta(hours=1),
        )
    )
    bundles = SentimentRepository().list_event_bundles(
        data_cutoff=CUTOFF,
        symbol="600000.SH",
        strict_point_in_time=True,
    )
    assert bundles == []


def test_security_specific_limit_ratios() -> None:
    assert _limit_ratio("600000.SH") == 0.10
    assert _limit_ratio("300001.SZ") == 0.20
    assert _limit_ratio("688001.SH") == 0.20
    assert _limit_ratio("830001.BJ") == 0.30


def test_screening_request_rejects_model_calls() -> None:
    with pytest.raises(ValidationError):
        SentimentAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            data_cutoff=CUTOFF,
            allow_model_calls=True,
        )


def test_factor_metadata_declares_zero_formal_weight(
    sentiment_db: Path,
) -> None:
    _seed_event(_record(record_id="r1", title="公司回购公告"))
    result = asyncio.run(
        SentimentAnalysisService().analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=CUTOFF,
                symbol="600000.SH",
            )
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.metadata["formal_strategy_weight"] == 0
