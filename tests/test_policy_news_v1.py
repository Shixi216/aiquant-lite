from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from config.settings import settings
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from database.migrations.v0104_policy_news_v1 import apply_migration
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.event_cluster_service import EventClusterService
from router.schemas import RouterInvokeResponse
from scripts.backfill_policy_news import run_backfill
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.decision_support.risk_rules.rules import evaluate_strategy_risk
from trading.research.policy_news.extractor import (
    PolicyNewsExtractor,
    assess_text_completeness,
)
from trading.research.policy_news.implementation_status import (
    classify_implementation,
)
from trading.research.policy_news.policy import (
    implementation_weights,
    policy_for,
)
from trading.research.policy_news.relevance import map_relevance
from trading.research.policy_news.repository import PolicyNewsRepository
from trading.research.policy_news.schemas import (
    ImplementationStatus,
    ModelPolicyExtraction,
    PolicyEventCategory,
    PolicyEventType,
    PolicyFactType,
    PolicyImpactHorizon,
    PolicyNewsAnalyzeRequest,
    PolicyRiskFlag,
    PolicyVerificationStatus,
    TextCompleteness,
)
from trading.research.policy_news.scorer import (
    calculate_policy_news_score,
)
from trading.research.policy_news.service import PolicyNewsAnalysisService
from trading.research.policy_news.source_ranker import rank_source
from trading.research.sentiment.service import SentimentAnalysisService
from trading.schemas import AnalysisMode


TZ = ZoneInfo("Asia/Shanghai")
EVENT_TIME = datetime(2026, 7, 1, 9, tzinfo=TZ)
FETCHED_AT = datetime(2026, 7, 1, 10, tzinfo=TZ)
CUTOFF = datetime(2026, 7, 1, 23, 59, tzinfo=TZ)


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
def policy_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "policy.duckdb"
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
    source_url: str | None = "https://example.com/source",
    verified: bool = True,
    event_time: datetime = EVENT_TIME,
    fetched_at: datetime = FETCHED_AT,
    content: str = "",
    full_text: str = "",
    publication: str | None = "2026-07-01",
    sector: str | None = None,
) -> MarketRecord:
    payload: dict[str, object] = {
        "symbol": symbol,
        "title": title,
        "publisher": source_name,
        "short_name": "测试公司",
    }
    if content:
        payload["content"] = content
    if full_text:
        payload["full_text"] = full_text
    if publication:
        payload[
            (
                "announcement_date"
                if data_type == DataType.ANNOUNCEMENT
                else "published_at"
            )
        ] = publication
    if sector:
        payload["sector"] = sector
    return MarketRecord(
        record_id=record_id,
        symbol=symbol,
        data_type=data_type,
        event_time=event_time,
        fetched_at=fetched_at,
        source_name=source_name,
        source_url=source_url,
        source_level=source_level,
        verified=verified,
        content_hash=_hash(payload),
        data=payload,
    )


def _seed(*records: MarketRecord):
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)
    cluster = EventClusterService().cluster(list(records))[0]
    bundle = PolicyNewsRepository().get_event_bundle(
        cluster.event_cluster_id
    )
    assert bundle is not None
    return bundle


async def _analyze(
    bundle,
    *,
    mode: AnalysisMode = AnalysisMode.RESEARCH,
    persist: bool = False,
    models: bool = False,
    cutoff: datetime = CUTOFF,
    flags: list[PolicyRiskFlag] | None = None,
):
    return await PolicyNewsAnalysisService().analyze_event(
        bundle,
        analysis_mode=mode,
        data_cutoff=cutoff,
        allow_model_calls=models,
        persist=persist,
        extra_risk_flags=flags,
    )


def _model_payload(
    *,
    event_type: PolicyEventType = PolicyEventType.MAJOR_CONTRACT,
    category: PolicyEventCategory = PolicyEventCategory.CORPORATE_MAJOR_EVENT,
    direction: int = 1,
    intensity: float = 0.7,
    confidence: float = 0.8,
    summary: str = "公告明确披露重大合同",
) -> dict:
    return {
        "event_category": category.value,
        "event_type": event_type.value,
        "direction": direction,
        "intensity": intensity,
        "confidence": confidence,
        "fact_type": PolicyFactType.COMPANY_ANNOUNCEMENT.value,
        "implementation_status_candidate": (
            ImplementationStatus.ANNOUNCED.value
        ),
        "impact_horizon": PolicyImpactHorizon.MEDIUM_TERM.value,
        "affected_symbols": ["600000.SH"],
        "affected_sectors": [],
        "summary": summary,
        "key_facts": ["公告明确披露重大合同"],
        "amounts": [],
        "dates": [],
        "entities": ["测试公司"],
        "conditions": [],
    }


class _FakeProvider:
    provider_name = "fake"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse:
        self.calls.append(role)
        content = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if content == "__FAIL__":
            raise RuntimeError("provider unavailable")
        return RouterInvokeResponse(
            role=role,
            provider=self.provider_name,
            model="test-model",
            content=content,
            latency_ms=5,
            usage={"input_tokens": 10, "output_tokens": 5},
        )


def _backfill_args(
    tmp_path: Path,
    *,
    apply: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        dry_run=not apply,
        apply=apply,
        category=None,
        symbol=None,
        start_time=None,
        end_time=None,
        limit=None,
        resume=None,
        skip_model_calls=True,
        enable_model_calls=False,
        max_model_calls=0,
        report_path=tmp_path / ("apply.json" if apply else "dry.json"),
    )


# A. Event responsibility and shared event reuse


def test_01_reuses_event_clusters_without_second_event_master(
    policy_db: Path,
) -> None:
    with get_connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }
    assert "event_clusters" in tables
    assert "policy_news_events" not in tables


@pytest.mark.asyncio
async def test_02_same_event_has_one_idempotent_policy_analysis(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="a2", title="关于重大合同的公告")
    )
    first = await _analyze(bundle, persist=True)
    second = await _analyze(bundle, persist=True)
    assert first.policy_analysis_id == second.policy_analysis_id
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM policy_news_event_analyses"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_03_media_repost_does_not_duplicate_direction_score(
    policy_db: Path,
) -> None:
    first = _record(
        record_id="a3a",
        title="公司签订重大合同",
        source_level=SourceLevel.OFFICIAL,
        data_type=DataType.FINANCE_NEWS,
        publication="2026-07-01T09:00:00+08:00",
    )
    second = _record(
        record_id="a3b",
        title="公司签订重大合同",
        source_level=SourceLevel.MEDIA,
        source_name="普通财经媒体",
        data_type=DataType.FINANCE_NEWS,
        publication="2026-07-01T09:00:00+08:00",
    )
    second = second.model_copy(update={"content_hash": first.content_hash})
    bundle = _seed(first, second)
    base = await _analyze(bundle)
    assert base.policy_news_score <= settings.policy_news_max_single_event_contribution
    assert bundle.source_count == 2
    assert base.evidence_ids.count(bundle.event_cluster_id) == 1


@pytest.mark.asyncio
async def test_04_shared_sentiment_event_is_marked(policy_db: Path) -> None:
    bundle = _seed(
        _record(record_id="a4", title="关于重大合同的公告")
    )
    await SentimentAnalysisService().analyze_event(
        bundle,
        data_cutoff=CUTOFF,
        allow_model_calls=False,
        persist=True,
    )
    analysis = await _analyze(bundle)
    assert analysis.shared_sentiment_analysis_ids
    assert PolicyRiskFlag.SHARED_SENTIMENT_EVENT in analysis.risk_flags


@pytest.mark.asyncio
async def test_05_sentiment_and_policy_share_evidence_but_outputs_are_independent(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="a5", title="公司签订重大合同")
    )
    sentiment = await SentimentAnalysisService().analyze_event(
        bundle,
        data_cutoff=CUTOFF,
        allow_model_calls=False,
        persist=True,
    )
    policy = await _analyze(bundle)
    assert bundle.event_cluster_id in sentiment.evidence_ids
    assert bundle.event_cluster_id in policy.evidence_ids
    assert sentiment.sentiment_analysis_id != policy.policy_analysis_id


@pytest.mark.asyncio
async def test_06_duplicate_suspect_is_not_auto_merged(
    policy_db: Path,
) -> None:
    first = _seed(
        _record(record_id="a6a", title="重大合同事项一")
    )
    second = _seed(
        _record(
            record_id="a6b",
            title="重大合同事项二",
            event_time=EVENT_TIME + timedelta(minutes=1),
        )
    )
    analysis = await _analyze(
        first,
        flags=[PolicyRiskFlag.DUPLICATE_SUSPECTED],
    )
    assert first.event_cluster_id != second.event_cluster_id
    assert PolicyRiskFlag.DUPLICATE_SUSPECTED in analysis.risk_flags


# B. Text and model boundaries


def test_07_full_text_is_recognized(policy_db: Path) -> None:
    bundle = _seed(
        _record(
            record_id="b7",
            title="重大合同公告",
            full_text="公司已经签订重大合同，合同条件以正式文本为准。",
        )
    )
    assert assess_text_completeness(bundle) == TextCompleteness.FULL_TEXT


@pytest.mark.asyncio
async def test_08_title_only_does_not_invent_amounts(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(
            record_id="b8",
            title="重大合同公告",
            publication=None,
        )
    )
    analysis = await _analyze(bundle)
    assert analysis.amounts == []
    assert analysis.conditions == []


@pytest.mark.asyncio
async def test_09_partial_text_caps_confidence(policy_db: Path) -> None:
    bundle = _seed(
        _record(
            record_id="b9",
            title="重大合同公告",
            content="媒体仅提供部分正文，公司披露重大合同。",
        )
    )
    analysis = await _analyze(bundle)
    assert analysis.text_completeness == TextCompleteness.PARTIAL_TEXT
    assert analysis.model_confidence <= settings.policy_news_partial_text_confidence_cap


@pytest.mark.asyncio
async def test_10_qwen_output_passes_strict_schema(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(
            record_id="b10",
            title="重大合同公告",
            full_text="公司公告签订重大合同。",
        )
    )
    fake = _FakeProvider([json.dumps(_model_payload(), ensure_ascii=False)])
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name == "qwen",
    )
    result = await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    assert result.used_model is True
    assert fake.calls == ["announcement_verifier"]


@pytest.mark.asyncio
async def test_11_longcat_structures_media_content(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(
            record_id="b11",
            title="公司签订重大合同",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            source_name="财经媒体",
            content="报道援引公司公告称已签订重大合同。",
            publication="2026-07-01T09:00:00+08:00",
        )
    )
    fake = _FakeProvider([json.dumps(_model_payload(), ensure_ascii=False)])
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name == "longcat",
    )
    await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    assert fake.calls == ["news_processor"]


@pytest.mark.asyncio
async def test_12_invalid_json_repairs_at_most_once(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(record_id="b12", title="重大合同公告")
    )
    fake = _FakeProvider(
        ["not-json", json.dumps(_model_payload(), ensure_ascii=False)]
    )
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name == "qwen",
    )
    result = await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    assert len(fake.calls) == 2
    assert result.used_model is True


@pytest.mark.asyncio
async def test_13_model_failure_falls_back_safely(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(record_id="b13", title="重大合同公告")
    )
    fake = _FakeProvider(["__FAIL__"])
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda _: True,
    )
    result = await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    assert result.used_model is False
    assert PolicyRiskFlag.MODEL_OUTPUT_INVALID in result.risk_flags


@pytest.mark.asyncio
async def test_14_no_api_key_keeps_service_available(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(record_id="b14", title="重大合同公告")
    )
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda _: False,
    )
    analysis = await _analyze(bundle, models=True)
    assert analysis.event_type == PolicyEventType.MAJOR_CONTRACT
    assert analysis.model_call_ids == []


@pytest.mark.asyncio
async def test_15_deepseek_runs_only_for_escalation(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(record_id="b15", title="证监会立案调查公告")
    )
    payload = _model_payload(
        event_type=PolicyEventType.REGULATORY_INVESTIGATION,
        category=PolicyEventCategory.REGULATORY_ACTION,
        direction=-1,
        intensity=0.9,
    )
    fake = _FakeProvider(
        [
            json.dumps(payload, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
        ]
    )
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name in {"qwen", "deepseek"},
    )
    await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    assert fake.calls == ["announcement_verifier", "risk_controller"]


def test_16_llm_trade_instruction_is_rejected() -> None:
    payload = _model_payload(summary="建议买入并设置目标价")
    with pytest.raises(ValidationError):
        ModelPolicyExtraction.model_validate(payload)


@pytest.mark.asyncio
async def test_17_model_call_audit_is_complete(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _seed(
        _record(record_id="b17", title="重大合同公告")
    )
    fake = _FakeProvider([json.dumps(_model_payload(), ensure_ascii=False)])
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name == "qwen",
    )
    result = await PolicyNewsExtractor(
        provider_factory=lambda _: fake
    ).extract(bundle, allow_model_calls=True)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT prompt_version, input_hash, retry_count, schema_validation
            FROM model_calls WHERE call_id = ?
            """,
            [result.model_call_ids[0]],
        ).fetchone()
    assert row[0]
    assert len(row[1]) == 64
    assert row[2] == 0
    assert row[3] == "VALID"


# C. Source authority and implementation status


def test_18_official_source_weight_exceeds_media(
    policy_db: Path,
) -> None:
    official = _seed(
        _record(record_id="c18a", title="重大合同公告")
    )
    media = _seed(
        _record(
            record_id="c18b",
            title="媒体报道重大合同",
            data_type=DataType.FINANCE_NEWS,
            source_level=SourceLevel.MEDIA,
            source_name="普通财经媒体",
        )
    )
    assert rank_source(official).authority_weight > rank_source(media).authority_weight


def test_19_announced_weight_is_below_executed() -> None:
    weights = implementation_weights()
    assert weights["ANNOUNCED"] < weights["EXECUTED"]


def test_20_rumor_weight_is_below_announced() -> None:
    weights = implementation_weights()
    assert weights["RUMOR"] < weights["ANNOUNCED"]


def test_21_terminated_positive_score_is_zero() -> None:
    result = calculate_policy_news_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=0,
        verification_weight=1,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.VERIFIED_OFFICIAL,
        implementation_status=ImplementationStatus.TERMINATED,
    )
    assert result.score == 0


def test_22_retracted_state_has_zero_weight_and_history_semantics() -> None:
    result = classify_implementation(
        text="政策文件已撤回",
        event_category=PolicyEventCategory.INDUSTRIAL_SUPPORT,
        event_time=EVENT_TIME,
        official_source=True,
    )
    assert result.status == ImplementationStatus.RETRACTED
    assert result.weight == 0
    assert result.termination_time == EVENT_TIME


@pytest.mark.asyncio
async def test_23_missing_primary_url_sets_risk(policy_db: Path) -> None:
    bundle = _seed(
        _record(
            record_id="c23",
            title="重大合同公告",
            source_url=None,
        )
    )
    analysis = await _analyze(bundle)
    assert PolicyRiskFlag.PRIMARY_SOURCE_MISSING in analysis.risk_flags


@pytest.mark.asyncio
async def test_24_status_change_creates_append_only_versions(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="c24", title="重大项目公告")
    )
    first_source = bundle.primary_source.model_copy(
        update={
            "payload": {
                **bundle.primary_source.payload,
                "title": "重大投资项目公告",
            }
        }
    )
    second_source = bundle.primary_source.model_copy(
        update={
            "payload": {
                **bundle.primary_source.payload,
                "title": "重大投资项目已经完成",
            }
        }
    )
    first_bundle = bundle.model_copy(
        update={
            "canonical_title": "重大投资项目公告",
            "source_records": [first_source],
            "cluster_hash": _hash("first-status"),
        }
    )
    second_bundle = bundle.model_copy(
        update={
            "canonical_title": "重大投资项目已经完成",
            "source_records": [second_source],
            "cluster_hash": _hash("second-status"),
        }
    )
    first = await _analyze(first_bundle, persist=True)
    second = await _analyze(
        second_bundle,
        persist=True,
        cutoff=CUTOFF + timedelta(minutes=1),
    )
    assert first.policy_analysis_id != second.policy_analysis_id
    assert PolicyNewsRepository().get_analysis(first.policy_analysis_id)


# D. Point-in-time safety


@pytest.mark.asyncio
async def test_25_event_after_cutoff_is_rejected(policy_db: Path) -> None:
    bundle = _seed(
        _record(
            record_id="d25",
            title="重大合同公告",
            fetched_at=CUTOFF + timedelta(hours=1),
        )
    )
    with pytest.raises(ValueError, match="not available"):
        await _analyze(bundle)


@pytest.mark.asyncio
async def test_26_fetched_at_does_not_replace_publication_time(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(
            record_id="d26",
            title="重大合同公告",
            publication=None,
        )
    )
    analysis = await _analyze(bundle)
    assert analysis.publication_time is None
    assert analysis.fetched_at == FETCHED_AT


@pytest.mark.asyncio
async def test_27_historical_analysis_does_not_use_later_status(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="d27", title="重大投资项目公告")
    )
    historical = await _analyze(bundle, persist=True)
    later = bundle.model_copy(
        update={"canonical_title": "重大投资项目已经完成"}
    )
    await _analyze(
        later,
        persist=True,
        cutoff=CUTOFF + timedelta(minutes=1),
    )
    stored = PolicyNewsRepository().get_analysis(
        historical.policy_analysis_id
    )
    assert stored is not None
    assert stored.implementation_status == historical.implementation_status


@pytest.mark.asyncio
async def test_28_decision_missing_publication_has_zero_directional_score(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(
            record_id="d28",
            title="重大合同公告",
            publication=None,
        )
    )
    analysis = await _analyze(bundle, mode=AnalysisMode.DECISION)
    assert analysis.direction == 0
    assert analysis.policy_news_score == 0


@pytest.mark.asyncio
async def test_29_research_allows_unverified_shadow_reference(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(
            record_id="d29",
            title="重大合同公告",
            publication=None,
        )
    )
    analysis = await _analyze(bundle, mode=AnalysisMode.RESEARCH)
    assert analysis.shadow_mode is True
    assert PolicyRiskFlag.PUBLICATION_TIME_MISSING in analysis.risk_flags


def test_30_repository_excludes_future_news(policy_db: Path) -> None:
    _seed(
        _record(
            record_id="d30",
            title="重大合同公告",
            event_time=CUTOFF + timedelta(days=1),
            fetched_at=CUTOFF + timedelta(days=1, hours=1),
            publication="2026-07-02",
        )
    )
    bundles = PolicyNewsRepository().list_event_bundles(
        data_cutoff=CUTOFF,
        strict_point_in_time=True,
    )
    assert bundles == []


# E. Relevance and deterministic scoring


def test_31_direct_symbol_link_has_high_relevance(policy_db: Path) -> None:
    bundle = _seed(
        _record(record_id="e31", title="重大合同公告")
    )
    result = map_relevance(
        bundle,
        candidate_symbols=[],
        candidate_sectors=[],
    )
    assert result.symbols[0].relevance_weight == 1


def test_32_sector_only_does_not_expand_to_concept_stocks(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(
            record_id="e32",
            title="产业规划发布",
            sector="半导体",
        )
    )
    result = map_relevance(
        bundle.model_copy(update={"symbols": []}),
        candidate_symbols=[],
        candidate_sectors=["半导体"],
    )
    assert result.symbols == ()
    assert result.sectors[0].sector == "半导体"


def test_33_model_symbol_without_business_evidence_is_low(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="e33", title="产业规划发布")
    )
    result = map_relevance(
        bundle.model_copy(update={"symbols": []}),
        candidate_symbols=["600001.SH"],
        candidate_sectors=[],
    )
    assert result.symbols[0].relevance_weight == 0.25
    assert PolicyRiskFlag.SYMBOL_RELEVANCE_LOW in result.risk_flags


def test_34_missing_sector_mapping_is_flagged(policy_db: Path) -> None:
    bundle = _seed(
        _record(record_id="e34", title="重大合同公告")
    )
    result = map_relevance(
        bundle,
        candidate_symbols=[],
        candidate_sectors=["概念板块"],
    )
    assert result.sectors == ()
    assert PolicyRiskFlag.SECTOR_MAPPING_MISSING in result.risk_flags


def test_35_score_formula_is_exact() -> None:
    result = calculate_policy_news_score(
        direction=1,
        intensity=0.8,
        model_confidence=0.9,
        source_authority_weight=0.75,
        freshness=0.8,
        implementation_weight=0.7,
        verification_weight=0.9,
        relevance_weight=0.5,
        verification_status=PolicyVerificationStatus.VERIFIED_MULTI_SOURCE,
        implementation_status=ImplementationStatus.ANNOUNCED,
    )
    expected = 0.8 * 0.9 * 0.75 * 0.8 * 0.7 * 0.9 * 0.5
    assert result.score == pytest.approx(expected)


def test_36_score_is_bounded_to_unit_interval() -> None:
    result = calculate_policy_news_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.VERIFIED_OFFICIAL,
        implementation_status=ImplementationStatus.EXECUTED,
    )
    assert -1 <= result.score <= 1


def test_37_neutral_direction_scores_zero() -> None:
    result = calculate_policy_news_score(
        direction=0,
        intensity=1,
        model_confidence=1,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.VERIFIED_OFFICIAL,
        implementation_status=ImplementationStatus.EXECUTED,
    )
    assert result.score == 0


def test_38_conflict_scores_zero() -> None:
    result = calculate_policy_news_score(
        direction=-1,
        intensity=1,
        model_confidence=1,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=1,
        verification_weight=0,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.CONFLICT,
        implementation_status=ImplementationStatus.ANNOUNCED,
    )
    assert result.score == 0


def test_39_missing_component_is_not_defaulted_to_one() -> None:
    result = calculate_policy_news_score(
        direction=1,
        intensity=1,
        model_confidence=None,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.VERIFIED_OFFICIAL,
        implementation_status=ImplementationStatus.EXECUTED,
    )
    assert result.score == 0
    assert "model_confidence" in result.missing_components


def test_40_single_event_contribution_is_capped() -> None:
    result = calculate_policy_news_score(
        direction=1,
        intensity=1,
        model_confidence=1,
        source_authority_weight=1,
        freshness=1,
        implementation_weight=1,
        verification_weight=1,
        relevance_weight=1,
        verification_status=PolicyVerificationStatus.VERIFIED_OFFICIAL,
        implementation_status=ImplementationStatus.EXECUTED,
    )
    assert result.score == settings.policy_news_max_single_event_contribution


# F. Three-mode isolation


@pytest.mark.asyncio
async def test_41_screening_never_calls_llm(policy_db: Path) -> None:
    fake = _FakeProvider([json.dumps(_model_payload())])
    service = PolicyNewsAnalysisService(
        extractor=PolicyNewsExtractor(provider_factory=lambda _: fake)
    )
    await service.analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert fake.calls == []


def test_42_screening_disallows_external_fetch() -> None:
    policy = policy_for(AnalysisMode.SCREENING)
    assert policy.allow_external_fetch is False
    with pytest.raises(ValidationError):
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
            allow_external_fetch=True,
        )


@pytest.mark.asyncio
async def test_43_screening_missing_policy_data_still_returns(
    policy_db: Path,
) -> None:
    result = await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert result.symbol_snapshot is None
    assert "policy_news_symbol_snapshot" in result.missing_fields


@pytest.mark.asyncio
async def test_44_screening_does_not_generate_factor(policy_db: Path) -> None:
    result = await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert result.factor_output is None


@pytest.mark.asyncio
async def test_45_screening_does_not_generate_decision_packet(
    policy_db: Path,
) -> None:
    before = 0
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM decision_packets"
        ).fetchone()[0]
    await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.SCREENING,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM decision_packets"
        ).fetchone()[0]
    assert before == after


def test_46_research_allows_controlled_models_and_fetch() -> None:
    policy = policy_for(AnalysisMode.RESEARCH)
    assert policy.allow_model_calls is True
    assert policy.allow_external_fetch is True


@pytest.mark.asyncio
async def test_47_research_factor_is_always_shadow(policy_db: Path) -> None:
    _seed(_record(record_id="f47", title="重大合同公告"))
    result = await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode is True


@pytest.mark.asyncio
async def test_48_decision_strictly_enforces_cutoff(policy_db: Path) -> None:
    _seed(
        _record(
            record_id="f48",
            title="重大合同公告",
            fetched_at=CUTOFF + timedelta(hours=1),
        )
    )
    result = await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.DECISION,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert result.analyses == []


@pytest.mark.asyncio
async def test_49_decision_factor_is_always_shadow(policy_db: Path) -> None:
    _seed(_record(record_id="f49", title="重大合同公告"))
    result = await PolicyNewsAnalysisService().analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.DECISION,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode is True
    assert result.factor_output.metadata["formal_strategy_weight"] == 0


def test_50_policy_factor_does_not_change_formal_actions() -> None:
    source = inspect.getsource(_generate_strategy).casefold()
    assert "policy" not in source
    assert "0.6" in source and "0.4" in source


def test_51_policy_factor_does_not_change_hard_veto() -> None:
    source = inspect.getsource(evaluate_strategy_risk).casefold()
    assert "policy" not in source
    assert "veto" in source


def test_52_mode_policies_do_not_cross_call() -> None:
    screening = policy_for(AnalysisMode.SCREENING)
    research = policy_for(AnalysisMode.RESEARCH)
    decision = policy_for(AnalysisMode.DECISION)
    assert screening.use_existing_snapshot is True
    assert research.use_existing_snapshot is False
    assert decision.allow_model_calls is False
    assert decision.allow_external_fetch is False


# G. Data, migration, audit and backfill


def test_53_migration_is_idempotent_twice(tmp_path: Path) -> None:
    connection = duckdb.connect(str(tmp_path / "migration.duckdb"))
    try:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_54_raw_data_records_are_unchanged(policy_db: Path) -> None:
    bundle = _seed(
        _record(record_id="g54", title="重大合同公告")
    )
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    await _analyze(bundle, persist=True)
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    assert before == after


@pytest.mark.asyncio
async def test_55_event_cluster_count_does_not_increase(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="g55", title="重大合同公告")
    )
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM event_clusters"
        ).fetchone()[0]
    await _analyze(bundle, persist=True)
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM event_clusters"
        ).fetchone()[0]
    assert before == after


@pytest.mark.asyncio
async def test_56_sentiment_records_are_not_modified(policy_db: Path) -> None:
    bundle = _seed(
        _record(record_id="g56", title="重大合同公告")
    )
    await SentimentAnalysisService().analyze_event(
        bundle,
        data_cutoff=CUTOFF,
        allow_model_calls=False,
        persist=True,
    )
    with get_connection() as connection:
        before = connection.execute(
            """
            SELECT sentiment_analysis_id, input_snapshot_hash
            FROM sentiment_event_analyses ORDER BY sentiment_analysis_id
            """
        ).fetchall()
    await _analyze(bundle, persist=True)
    with get_connection() as connection:
        after = connection.execute(
            """
            SELECT sentiment_analysis_id, input_snapshot_hash
            FROM sentiment_event_analyses ORDER BY sentiment_analysis_id
            """
        ).fetchall()
    assert before == after


@pytest.mark.asyncio
async def test_57_policy_factor_evidence_resolves_to_raw_data(
    policy_db: Path,
) -> None:
    bundle = _seed(
        _record(record_id="g57", title="重大合同公告")
    )
    analysis = await _analyze(bundle, persist=True)
    assert FactorOutputRepository.resolve_evidence(
        analysis.policy_analysis_id
    )
    assert FactorOutputRepository.resolve_evidence("g57")


@pytest.mark.asyncio
async def test_58_factor_tracks_model_calls(
    policy_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(
        _record(
            record_id="g58",
            title="重大合同公告",
            full_text="公司公告签订重大合同。",
        )
    )
    fake = _FakeProvider([json.dumps(_model_payload(), ensure_ascii=False)])
    monkeypatch.setattr(
        "trading.research.policy_news.extractor._provider_ready",
        lambda name: name == "qwen",
    )
    service = PolicyNewsAnalysisService(
        extractor=PolicyNewsExtractor(provider_factory=lambda _: fake)
    )
    result = await service.analyze(
        PolicyNewsAnalyzeRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            symbol="600000.SH",
            data_cutoff=CUTOFF,
            allow_model_calls=True,
        )
    )
    assert result.factor_output is not None
    assert result.factor_output.model_call_ids


@pytest.mark.asyncio
async def test_59_backfill_dry_run_does_not_write(
    policy_db: Path,
    tmp_path: Path,
) -> None:
    _seed(_record(record_id="g59", title="重大合同公告"))
    report = await run_backfill(_backfill_args(tmp_path, apply=False))
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM policy_news_event_analyses"
        ).fetchone()[0]
    assert report["mode"] == "DRY_RUN"
    assert count == 0


@pytest.mark.asyncio
async def test_60_backfill_apply_twice_is_idempotent(
    policy_db: Path,
    tmp_path: Path,
) -> None:
    _seed(_record(record_id="g60", title="重大合同公告"))
    args = _backfill_args(tmp_path, apply=True)
    await run_backfill(args)
    with get_connection() as connection:
        first = connection.execute(
            "SELECT COUNT(*) FROM policy_news_event_analyses"
        ).fetchone()[0]
    await run_backfill(args)
    with get_connection() as connection:
        second = connection.execute(
            "SELECT COUNT(*) FROM policy_news_event_analyses"
        ).fetchone()[0]
    assert first == second == 1


@pytest.mark.asyncio
async def test_61_one_bad_event_does_not_abort_backfill(
    policy_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _seed(_record(record_id="g61a", title="重大合同事项甲"))
    _seed(
        _record(
            record_id="g61b",
            title="重大合同事项乙",
            event_time=EVENT_TIME + timedelta(minutes=1),
        )
    )
    original = PolicyNewsAnalysisService.analyze_event

    async def selective_failure(self, bundle, **kwargs):
        if bundle.event_cluster_id == bad.event_cluster_id:
            raise ValueError("bad historical record")
        return await original(self, bundle, **kwargs)

    monkeypatch.setattr(
        PolicyNewsAnalysisService,
        "analyze_event",
        selective_failure,
    )
    report = await run_backfill(_backfill_args(tmp_path, apply=True))
    assert report["failed_count"] == 1
    assert report["processed_count"] == 2
    assert report["success_count"] == 1


def test_62_decision_packet_immutability_code_is_unchanged() -> None:
    from trading.decision_support.decision_packets.repository import (
        DecisionRepository,
    )

    source = inspect.getsource(DecisionRepository)
    assert "FINAL" in source
    assert "payload" in source
    assert "policy_news" not in source


def test_63_no_live_or_broker_code_added() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "xtquant",
        "qmt_path",
        "broker_password",
        "live_trading_enabled",
    )
    for path in (
        root / "trading" / "research" / "policy_news"
    ).glob("*.py"):
        source = path.read_text(encoding="utf-8").casefold()
        assert all(token not in source for token in forbidden)
