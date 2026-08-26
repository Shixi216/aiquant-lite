from __future__ import annotations

import hashlib
from datetime import date, datetime
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from trading.research.sentiment.v3.feature_assembly import (
    DatasetCutoffPolicy,
    FeatureQuality,
    MissingReason,
    PacketQualityStatus,
    PacketType,
    ResearchDatasetBuilder,
    ResearchFeatureAssembler,
    SentimentV3ResearchShadow,
)
from trading.research.sentiment.v3.feature_registry import (
    FEATURE_REGISTRY_V1,
    ResearchStatus,
)
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    DragonTigerSnapshot,
    Finality,
    MarginFinancingSnapshot,
    MarketCapitalSnapshot,
    MarketSentimentSnapshot,
    QualityFlag,
    RecordSource,
    SCHEMA_VERSION,
    SectorCapitalSnapshot,
    SectorSentimentSnapshot,
    SectorType,
    SentimentSnapshotMeta,
    SnapshotType,
    SpeculationSentimentSnapshot,
    StockCapitalSnapshot,
    StockTradingSentimentSnapshot,
)
from trading.research.sentiment.v3.text_models import (
    EventIntelligenceRecord,
    EventReactionSnapshot,
    EventSession,
    EventType,
    ExtractionMethod,
    FactStatus,
    NewsSentimentSnapshot,
    Novelty,
    ReactionState,
    ReactionWindowType,
    TextDirection,
    TextHorizon,
    TextSourceQuality,
)


TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 13)
SOURCE = datetime(2026, 8, 13, 15, 0, tzinfo=TZ)
AVAILABLE = datetime(2026, 8, 13, 16, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 8, 13, 20, 0, tzinfo=TZ)
SYMBOL = "000001.SZ"
SECTOR = "IND:银行"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def meta(
    kind: SnapshotType,
    scope: str,
    *,
    available: datetime = AVAILABLE,
    finality: Finality = Finality.FINAL,
    record_source: RecordSource = RecordSource.TEST,
    revision: int = 1,
    corrects: str | None = None,
    flags: tuple[QualityFlag, ...] = (),
    suffix: str = "v1",
) -> SentimentSnapshotMeta:
    quality = list(flags)
    if finality in {Finality.INTRADAY, Finality.PRELIMINARY}:
        quality.append(QualityFlag.NOT_FINAL)
    source_time = min(SOURCE, available)
    digest = _digest(f"{kind.value}:{scope}:{suffix}:{record_source.value}")
    return SentimentSnapshotMeta(
        snapshot_id=f"{kind.value}_{scope}_{suffix}_{record_source.value}",
        snapshot_type=kind,
        trade_date=DAY,
        symbol_scope=scope,
        source="TEST_LOCAL",
        source_timestamp=source_time,
        available_time=available,
        data_cutoff=available,
        snapshot_time=available,
        finality=finality,
        data_version=suffix,
        schema_version=SCHEMA_VERSION,
        algorithm_version=f"test-{kind.value.lower()}-v1",
        content_hash=digest,
        coverage_ratio=1,
        quality_flags=quality,
        record_source=record_source,
        revision=revision,
        corrects_snapshot_id=corrects,
        created_at=available,
    )


def snapshots(
    *,
    record_source: RecordSource = RecordSource.TEST,
    finality: Finality = Finality.FINAL,
    available: datetime = AVAILABLE,
    dragon: bool | None = True,
    margin: bool | None = True,
) -> list[object]:
    market_meta = meta(
        SnapshotType.MARKET,
        "ALL_A",
        available=available,
        finality=finality,
        record_source=record_source,
    )
    sector_meta = meta(
        SnapshotType.SECTOR,
        SECTOR,
        available=available,
        finality=finality,
        record_source=record_source,
    )
    stock_meta = meta(
        SnapshotType.STOCK,
        SYMBOL,
        available=available,
        finality=finality,
        record_source=record_source,
    )
    return [
        MarketSentimentSnapshot(
            meta=market_meta,
            advancers=3000,
            decliners=2000,
            unchanged=100,
            advance_ratio=0.588,
            decline_ratio=0.392,
            advance_decline_ratio=1.5,
            breadth_strength=0.196,
            limit_up_count=50,
            limit_down_count=10,
            broken_board_count=12,
            broken_board_rate=0.1935,
            strong_stock_count=80,
            market_turnover=1e12,
            market_turnover_change=0.1,
        ),
        SpeculationSentimentSnapshot(
            meta=meta(
                SnapshotType.SPECULATION,
                "ALL_A",
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            first_board_count=30,
            second_board_count=10,
            third_board_count=5,
            high_board_count=2,
            max_board_height=6,
            sealed_limit_up_count=50,
            promotion_rate=0.3,
            yesterday_limit_up_avg_return=0.02,
            yesterday_limit_up_median_return=0.01,
            yesterday_board_success_rate=0.2,
            broken_board_repair_rate=0.1,
            high_position_negative_feedback=0.15,
        ),
        SectorSentimentSnapshot(
            meta=sector_meta,
            sector_id=SECTOR,
            sector_name="银行",
            sector_type=SectorType.INDUSTRY,
            universe_id="u1",
            universe_version="uv1",
            market_snapshot_id=market_meta.snapshot_id,
            membership_version="CURRENT_V1",
            advance_ratio=0.7,
            mean_return=0.03,
            median_return=0.025,
            turnover_change=0.2,
            relative_market_return=0.01,
            leader_return=0.08,
        ),
        StockTradingSentimentSnapshot(
            meta=stock_meta,
            symbol=SYMBOL,
            universe_id="u1",
            universe_version="uv1",
            industry_sector_id=SECTOR,
            return_pct=0.04,
            open_gap_pct=0.01,
            intraday_return_pct=0.03,
            amplitude=0.06,
            turnover_rate=0.02,
            volume_ratio=1.2,
            volume_change=0.3,
            turnover_change=0.25,
            close_location_value=0.8,
            high_to_close_drawdown=0.01,
            low_to_close_recovery=0.05,
            relative_market_return=0.02,
            relative_sector_return=0.01,
            is_limit_up=False,
            is_limit_down=False,
            is_broken_board=False,
            market_snapshot_id=market_meta.snapshot_id,
            sector_snapshot_id=sector_meta.snapshot_id,
        ),
        MarketCapitalSnapshot(
            meta=meta(
                SnapshotType.MARKET_CAPITAL,
                "ALL_A",
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            universe_id="u1",
            universe_version="uv1",
            market_snapshot_id=market_meta.snapshot_id,
            market_turnover=1e12,
            market_turnover_change=0.1,
            vendor_net_flow=1e9,
            vendor_main_net_flow=5e8,
            vendor_main_flow_ratio=0.005,
        ),
        SectorCapitalSnapshot(
            meta=meta(
                SnapshotType.SECTOR_CAPITAL,
                SECTOR,
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            sector_id=SECTOR,
            sector_name="银行",
            sector_type=SectorType.INDUSTRY,
            universe_id="u1",
            universe_version="uv1",
            membership_version="CURRENT_V1",
            sector_snapshot_id=sector_meta.snapshot_id,
            turnover_change=0.2,
            vendor_main_flow_sum=2e8,
            vendor_main_flow_median=1e6,
            relative_market_turnover=0.1,
        ),
        StockCapitalSnapshot(
            meta=meta(
                SnapshotType.STOCK_CAPITAL,
                SYMBOL,
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            symbol=SYMBOL,
            universe_id="u1",
            universe_version="uv1",
            industry_sector_id=SECTOR,
            turnover=1e9,
            turnover_change=0.25,
            turnover_rate=0.02,
            vendor_main_net_flow=1e7,
            vendor_main_flow_ratio=0.01,
            relative_market_flow=0.005,
            relative_sector_flow=0.002,
            stock_snapshot_id=stock_meta.snapshot_id,
        ),
        MarginFinancingSnapshot(
            meta=meta(
                SnapshotType.MARGIN_FINANCING,
                SYMBOL,
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            symbol=SYMBOL,
            universe_id="u1",
            universe_version="uv1",
            has_margin_data=margin,
            margin_balance=1e8 if margin else None,
            margin_buy=1e7 if margin else None,
            margin_repay=8e6 if margin else None,
            margin_net_buy=2e6 if margin else None,
        ),
        DragonTigerSnapshot(
            meta=meta(
                SnapshotType.DRAGON_TIGER,
                SYMBOL,
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            symbol=SYMBOL,
            universe_id="u1",
            universe_version="uv1",
            has_dragon_tiger=dragon,
            buy_amount=2e8 if dragon else None,
            sell_amount=1e8 if dragon else None,
            net_amount=1e8 if dragon else None,
            institution_net_amount=2e7 if dragon else None,
            institution_seat_count=2 if dragon else None,
        ),
        NewsSentimentSnapshot(
            meta=meta(
                SnapshotType.NEWS,
                SYMBOL,
                available=available,
                finality=finality,
                record_source=record_source,
            ),
            symbol=SYMBOL,
            window_start=available.replace(hour=0),
            window_end=available,
            document_count=3,
            unique_document_count=2,
            source_count=2,
            opinion_count=1,
            expectation_change_count=1,
            market_reaction_text_count=0,
            rumor_count=0,
            positive_count=1,
            negative_count=1,
            neutral_count=0,
            mixed_count=0,
            unknown_count=0,
            confidence_median=0.8,
            novel_document_count=2,
            duplicate_document_count=1,
        ),
    ]


def event(
    *,
    record_source: RecordSource = RecordSource.TEST,
    available: datetime = AVAILABLE,
    event_id: str = "event-1",
) -> EventIntelligenceRecord:
    return EventIntelligenceRecord(
        event_id=event_id,
        canonical_event_id=f"canonical-{event_id}",
        symbol=SYMBOL,
        event_type=EventType.BUYBACK,
        direction=TextDirection.POSITIVE,
        intensity=0.7,
        confidence=0.8,
        horizon=TextHorizon.SHORT,
        novelty=Novelty.NEW,
        available_time=available,
        event_session=EventSession.POST_MARKET,
        source_quality=TextSourceQuality.OFFICIAL_DISCLOSURE,
        fact_status=FactStatus.CONFIRMED,
        source_document_hashes=[_digest(event_id)],
        official_confirmation_exists=True,
        extraction_method=ExtractionMethod.STRUCTURED_AI,
        provider="qwen",
        model_name="qwen-test",
        prompt_version="P1",
        schema_version=SCHEMA_VERSION,
        algorithm_version="event-v1",
        processed_at=available,
        record_source=record_source,
    )


def reaction(
    *,
    record_source: RecordSource = RecordSource.TEST,
    available: datetime = AVAILABLE,
) -> EventReactionSnapshot:
    return EventReactionSnapshot(
        meta=meta(
            SnapshotType.EVENT_REACTION, SYMBOL, available=available, record_source=record_source
        ),
        event_id="event-1",
        canonical_event_id="canonical-event-1",
        event_direction=TextDirection.POSITIVE,
        event_available_time=available.replace(hour=8),
        event_session=EventSession.PRE_MARKET,
        reaction_start=available.replace(hour=9, minute=30),
        reaction_end=available.replace(hour=15),
        window_type=ReactionWindowType.EOD_REACTION,
        classification=ReactionState.CONFIRMED_POSITIVE,
        stock_return=0.04,
        market_return=0.02,
        sector_return=0.03,
        relative_market_return=0.02,
        relative_sector_return=0.01,
        volume_change=0.3,
        turnover_change=0.25,
        close_location_value=0.8,
    )


def populated_repo(tmp_path, *, record_source: RecordSource = RecordSource.TEST):
    repo = SentimentV3Repository(tmp_path / f"s2e-{record_source.value}.duckdb")
    repo.save_snapshots(snapshots(record_source=record_source))
    repo.save_event(event(record_source=record_source))
    repo.save_event_reaction(reaction(record_source=record_source))
    return repo


def assemble(repo, **kwargs):
    return ResearchFeatureAssembler(repository=repo).assemble(
        symbol=SYMBOL,
        decision_time=CUTOFF,
        cutoff=CUTOFF,
        trade_date=DAY,
        record_source=RecordSource.TEST,
        **kwargs,
    )


def test_registry_names_versions_units_methodology_and_weight_are_frozen():
    registry = FEATURE_REGISTRY_V1
    assert len(registry.definitions) == 132
    assert len({item.feature_name for item in registry.definitions}) == 132
    assert all(item.formal_weight == 0 for item in registry.definitions)
    assert all(item.unit and item.methodology_class for item in registry.definitions)
    assert registry.registry_version == "Sentiment_V3_Feature_Registry_V1"


def test_registry_duplicate_features_are_explicit_aliases():
    aliases = [item for item in FEATURE_REGISTRY_V1.definitions if item.canonical_feature]
    assert len(aliases) == 8
    assert any(
        item.feature_name == "capital.stock.turnover_change"
        and item.canonical_feature == "stock.turnover_change"
        for item in aliases
    )


def test_history_safe_excludes_unsafe_membership_forward_and_aliases():
    safe = FEATURE_REGISTRY_V1.history_safe()
    assert len(safe) == 39
    assert all(item.canonical_feature is None for item in safe)
    assert all(
        ResearchStatus.PIT_UNSAFE_FOR_HISTORY not in item.historical_support for item in safe
    )
    assert all(
        ResearchStatus.CURRENT_MEMBERSHIP_ONLY not in item.historical_support for item in safe
    )
    assert all(ResearchStatus.FORWARD_ONLY not in item.historical_support for item in safe)


def test_complete_packet_has_namespaces_ids_lineage_and_no_score(tmp_path):
    packet = assemble(populated_repo(tmp_path))
    assert packet.quality_status == PacketQualityStatus.COMPLETE
    assert packet.market_snapshot_id and packet.stock_snapshot_id and packet.event_ids
    assert packet.features["market"]["advancers"] == 3000
    assert packet.features["stock"]["relative_sector_return"] == 0.01
    assert packet.features["capital"]["stock"]["vendor_main_net_flow"] == 1e7
    assert packet.features["event"]["event_count"] == 1
    assert packet.features["reaction"]["relative_sector_return"] == 0.01
    assert packet.feature_lineage["stock.return_pct"].snapshot_id == packet.stock_snapshot_id
    assert "sentiment_score" not in packet.model_dump()
    assert packet.formal_weight == 0


def test_partial_packet_survives_missing_modules_and_does_not_impute_zero(tmp_path):
    repo = SentimentV3Repository(tmp_path / "partial.duckdb")
    repo.save_snapshot(snapshots()[0])
    packet = assemble(repo)
    assert packet.quality_status == PacketQualityStatus.PARTIAL
    assert packet.features["stock"]["return_pct"] is None
    assert packet.features["capital"]["stock"]["vendor_main_net_flow"] is None
    assert packet.feature_missing_reasons["stock.return_pct"] == MissingReason.PIT_UNAVAILABLE


@pytest.mark.parametrize(
    "missing_kind",
    [
        SnapshotType.MARKET,
        SnapshotType.SECTOR,
        SnapshotType.STOCK_CAPITAL,
        SnapshotType.NEWS,
    ],
)
def test_each_missing_module_remains_explicit(tmp_path, missing_kind):
    repo = SentimentV3Repository(tmp_path / f"missing-{missing_kind.value}.duckdb")
    values = [item for item in snapshots() if item.meta.snapshot_type != missing_kind]
    repo.save_snapshots(values)
    packet = assemble(repo)
    assert packet.quality_status == PacketQualityStatus.PARTIAL
    assert any(
        item.reason in {MissingReason.PIT_UNAVAILABLE, MissingReason.NOT_APPLICABLE}
        for item in packet.missing_modules
    )


def test_no_dragon_tiger_and_provider_failure_are_distinct(tmp_path):
    no_list = SentimentV3Repository(tmp_path / "no-list.duckdb")
    no_list.save_snapshots(snapshots(dragon=False))
    packet = assemble(no_list)
    assert any(item.reason == MissingReason.NO_DRAGON_TIGER for item in packet.missing_modules)
    assert (
        packet.feature_missing_reasons["capital.dragon_tiger.net_amount"]
        == MissingReason.NO_DRAGON_TIGER
    )

    failed = SentimentV3Repository(tmp_path / "failed.duckdb")
    values = snapshots(dragon=None)
    dragon_value = next(
        item for item in values if item.meta.snapshot_type == SnapshotType.DRAGON_TIGER
    )
    index = values.index(dragon_value)
    values[index] = dragon_value.model_copy(
        update={
            "meta": dragon_value.meta.model_copy(
                update={"quality_flags": [QualityFlag.SOURCE_UNAVAILABLE, QualityFlag.PARTIAL_DATA]}
            )
        }
    )
    failed.save_snapshots(values)
    packet = assemble(failed)
    assert any(item.reason == MissingReason.SOURCE_UNAVAILABLE for item in packet.missing_modules)


def test_no_margin_eligibility_is_not_zero(tmp_path):
    repo = SentimentV3Repository(tmp_path / "no-margin.duckdb")
    repo.save_snapshots(snapshots(margin=False))
    packet = assemble(repo)
    assert packet.features["capital"]["margin"]["margin_balance"] is None
    assert (
        packet.feature_missing_reasons["capital.margin.margin_balance"]
        == MissingReason.NO_MARGIN_ELIGIBILITY
    )


def test_cutoff_cannot_see_future_final_but_intraday_packet_can_use_intraday(tmp_path):
    repo = SentimentV3Repository(tmp_path / "finality.duckdb")
    intraday_time = datetime(2026, 8, 13, 10, 0, tzinfo=TZ)
    repo.save_snapshots(snapshots(finality=Finality.INTRADAY, available=intraday_time))
    repo.save_snapshots(snapshots(finality=Finality.FINAL, available=AVAILABLE))
    cutoff = datetime(2026, 8, 13, 10, 30, tzinfo=TZ)
    assembler = ResearchFeatureAssembler(repository=repo)
    eod = assembler.assemble(
        symbol=SYMBOL,
        decision_time=cutoff,
        cutoff=cutoff,
        trade_date=DAY,
        record_source=RecordSource.TEST,
        packet_type=PacketType.EOD_RESEARCH_PACKET,
    )
    intraday = assembler.assemble(
        symbol=SYMBOL,
        decision_time=cutoff,
        cutoff=cutoff,
        trade_date=DAY,
        record_source=RecordSource.TEST,
        packet_type=PacketType.FORWARD_INTRADAY_PACKET,
    )
    assert eod.market_snapshot_id is None
    assert intraday.market_snapshot_id is not None
    assert "FINAL" not in intraday.market_snapshot_id


def test_inferred_available_time_quality_is_preserved(tmp_path):
    repo = SentimentV3Repository(tmp_path / "inferred.duckdb")
    values = snapshots()
    news = next(item for item in values if item.meta.snapshot_type == SnapshotType.NEWS)
    values[values.index(news)] = news.model_copy(
        update={
            "meta": news.meta.model_copy(
                update={"quality_flags": [QualityFlag.AVAILABLE_TIME_INFERRED]}
            )
        }
    )
    repo.save_snapshots(values)
    packet = assemble(repo)
    assert FeatureQuality.INFERRED_TIME in packet.feature_quality["news.opinion_count"]


def test_corrected_revision_is_visible_only_after_its_available_time(tmp_path):
    repo = SentimentV3Repository(tmp_path / "corrected.duckdb")
    original = snapshots()[0]
    repo.save_snapshot(original)
    corrected_time = datetime(2026, 8, 13, 18, 0, tzinfo=TZ)
    corrected = original.model_copy(
        update={
            "advancers": 3100,
            "meta": meta(
                SnapshotType.MARKET,
                "ALL_A",
                available=corrected_time,
                finality=Finality.CORRECTED,
                revision=2,
                corrects=original.meta.snapshot_id,
                suffix="v2",
            ),
        }
    )
    repo.save_snapshot(corrected)
    assembler = ResearchFeatureAssembler(repository=repo)
    early = assembler.assemble(
        symbol=SYMBOL,
        decision_time=datetime(2026, 8, 13, 17, tzinfo=TZ),
        trade_date=DAY,
        record_source=RecordSource.TEST,
    )
    late = assembler.assemble(
        symbol=SYMBOL,
        decision_time=datetime(2026, 8, 13, 19, tzinfo=TZ),
        trade_date=DAY,
        record_source=RecordSource.TEST,
    )
    assert early.features["market"]["advancers"] == 3000
    assert late.features["market"]["advancers"] == 3100


def test_event_after_cutoff_and_unfinished_reaction_are_not_visible(tmp_path):
    repo = SentimentV3Repository(tmp_path / "event-pit.duckdb")
    repo.save_snapshots(snapshots())
    repo.save_event(event(available=datetime(2026, 8, 13, 21, tzinfo=TZ)))
    packet = assemble(repo)
    assert packet.event_ids == [] and packet.features["event"]["event_count"] == 0
    repo.save_event(event(event_id="visible", available=AVAILABLE))
    packet = assemble(repo)
    assert packet.event_ids and packet.reaction_ids == []
    assert any(
        item.reason == MissingReason.REACTION_NOT_AVAILABLE_YET for item in packet.missing_modules
    )


def test_multiple_events_aggregate_without_event_score(tmp_path):
    repo = SentimentV3Repository(tmp_path / "events.duckdb")
    repo.save_snapshots(snapshots())
    repo.save_event(event())
    repo.save_event(event(event_id="event-2"))
    packet = assemble(repo)
    assert packet.features["event"]["event_count"] == 2
    assert packet.features["event"]["official_confirmed_count"] == 2
    assert "event_score" not in packet.flat_features()


def test_news_attention_and_sentiment_namespaces_are_separate(tmp_path):
    packet = assemble(populated_repo(tmp_path))
    assert packet.features["news"]["opinion_count"] == 1
    assert packet.features["attention"]["document_count"] == 3


def test_history_safe_packet_only_contains_registry_safe_features(tmp_path):
    packet = ResearchFeatureAssembler(repository=populated_repo(tmp_path)).assemble(
        symbol=SYMBOL,
        decision_time=CUTOFF,
        trade_date=DAY,
        record_source=RecordSource.TEST,
        include_history_ready_only=True,
    )
    assert set(packet.feature_names) == {
        item.feature_name for item in FEATURE_REGISTRY_V1.history_safe()
    }
    assert "sector.relative_market_return" not in packet.feature_names


def test_dataset_id_and_hash_are_deterministic_and_has_no_labels(tmp_path):
    builder = ResearchDatasetBuilder(
        assembler=ResearchFeatureAssembler(repository=populated_repo(tmp_path))
    )
    kwargs = dict(
        symbols=[SYMBOL],
        date_from=DAY,
        date_to=DAY,
        cutoff_policy=DatasetCutoffPolicy.EOD,
        record_source=RecordSource.TEST,
        include_history_ready_only=True,
    )
    first = builder.build(**kwargs)
    second = builder.build(**kwargs)
    assert first.definition.dataset_id == second.definition.dataset_id
    assert first.definition.content_hash == second.definition.content_hash
    assert first.definition.contains_labels is False
    assert all("future_return" not in key for key in first.rows[0].features)


def test_real_test_replay_are_strictly_isolated(tmp_path):
    repo = SentimentV3Repository(tmp_path / "sources.duckdb")
    repo.save_snapshots(snapshots(record_source=RecordSource.TEST))
    repo.save_snapshots(snapshots(record_source=RecordSource.REPLAY))
    repo.save_event(event(record_source=RecordSource.TEST))
    repo.save_event(event(record_source=RecordSource.REPLAY, event_id="replay-event"))
    assembler = ResearchFeatureAssembler(repository=repo)
    test_packet = assembler.assemble(
        symbol=SYMBOL, decision_time=CUTOFF, trade_date=DAY, record_source=RecordSource.TEST
    )
    replay_packet = assembler.assemble(
        symbol=SYMBOL, decision_time=CUTOFF, trade_date=DAY, record_source=RecordSource.REPLAY
    )
    assert test_packet.record_source == RecordSource.TEST
    assert replay_packet.record_source == RecordSource.REPLAY
    assert test_packet.event_ids == ["event-1"]
    assert replay_packet.event_ids == ["replay-event"]


def test_same_canonical_event_has_independent_source_revision_chains(tmp_path):
    repo = SentimentV3Repository(tmp_path / "event-source-lineage.duckdb")
    test_event = event(record_source=RecordSource.TEST)
    replay_event = test_event.model_copy(
        update={
            "event_id": "event-replay-same-canonical",
            "record_source": RecordSource.REPLAY,
            "source_document_hashes": [_digest("replay-document")],
        }
    )
    saved_test = repo.save_event(test_event)
    saved_replay = repo.save_event(replay_event)
    assert saved_test.revision == saved_replay.revision == 1
    assert (
        repo.list_events_as_of(cutoff=CUTOFF, symbols=[SYMBOL], record_source=RecordSource.TEST)[
            0
        ].event_id
        == test_event.event_id
    )
    assert (
        repo.list_events_as_of(cutoff=CUTOFF, symbols=[SYMBOL], record_source=RecordSource.REPLAY)[
            0
        ].event_id
        == replay_event.event_id
    )


class CountingRepository:
    def __init__(self, inner):
        self.inner = inner
        self.snapshot_loads = 0
        self.event_loads = 0
        self.reaction_loads = 0

    def load_snapshots_as_of(self, **kwargs):
        self.snapshot_loads += 1
        return self.inner.load_snapshots_as_of(**kwargs)

    def list_events_as_of(self, **kwargs):
        self.event_loads += 1
        return self.inner.list_events_as_of(**kwargs)

    def list_event_reactions_as_of(self, **kwargs):
        self.reaction_loads += 1
        return self.inner.list_event_reactions_as_of(**kwargs)

    def save_feature_packets(self, packets):
        return self.inner.save_feature_packets(packets)

    def save_research_dataset(self, dataset):
        return self.inner.save_research_dataset(dataset)


def test_batch_assembly_uses_three_bulk_reads_not_per_symbol_n_plus_one(tmp_path):
    repo = populated_repo(tmp_path)
    counted = CountingRepository(repo)
    symbols = [f"{index:06d}.SZ" for index in range(1, 101)]
    packets = ResearchFeatureAssembler(repository=counted).assemble_many(
        symbols=symbols, decision_time=CUTOFF, trade_date=DAY, record_source=RecordSource.TEST
    )
    assert len(packets) == 100
    assert (counted.snapshot_loads, counted.event_loads, counted.reaction_loads) == (1, 1, 1)


def test_batch_write_and_shadow_stats_are_source_separated(tmp_path):
    repo = populated_repo(tmp_path)
    assembler = ResearchFeatureAssembler(repository=repo)
    packets = assembler.assemble_many(
        symbols=[SYMBOL, "000002.SZ"],
        decision_time=CUTOFF,
        trade_date=DAY,
        record_source=RecordSource.TEST,
        persist=True,
    )
    assert len(packets) == 2
    stats = repo.shadow_stats()
    assert stats["record_source_counts"]["TEST"] == 2


def test_research_shadow_has_no_action_or_execution_api(tmp_path):
    shadow = SentimentV3ResearchShadow(
        assembler=ResearchFeatureAssembler(repository=populated_repo(tmp_path))
    )
    assert not hasattr(shadow, "buy")
    assert not hasattr(shadow, "sell")
    assert not hasattr(shadow, "execute")


def test_single_and_hundred_symbol_assembly_remain_bounded(tmp_path):
    assembler = ResearchFeatureAssembler(repository=populated_repo(tmp_path))
    started = perf_counter()
    assembler.assemble(
        symbol=SYMBOL, decision_time=CUTOFF, trade_date=DAY, record_source=RecordSource.TEST
    )
    single_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    assembler.assemble_many(
        symbols=[f"{index:06d}.SZ" for index in range(1, 101)],
        decision_time=CUTOFF,
        trade_date=DAY,
        record_source=RecordSource.TEST,
    )
    hundred_ms = (perf_counter() - started) * 1000
    assert single_ms < 5000
    assert hundred_ms < 10000


def test_packet_models_do_not_expose_formal_decision_fields():
    names = set(type(FEATURE_REGISTRY_V1).model_fields)
    assert not ({"formal_score", "action", "confidence", "veto", "target_position"} & names)
    assert "BASELINE_REFERENCE" not in {
        item.methodology_class for item in FEATURE_REGISTRY_V1.definitions
    }
