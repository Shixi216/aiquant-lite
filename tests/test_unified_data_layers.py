from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

import database.migrations.v0100_unified_data as unified_migration
from config.settings import settings
from database.db import get_connection, initialize_database, insert_market_record
from database.migrations.v0100_unified_data import apply_migration
from data_hub.repositories import (
    CanonicalFinancialRepository,
    CanonicalMarketRepository,
    EventClusterRepository,
    EvidenceNotFoundError,
    FactorOutputRepository,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.unified import FactorOutput, FactorType, VerificationStatus
from data_hub.services.canonicalization_service import (
    CanonicalizationPolicy,
    CanonicalizationService,
)
from data_hub.services.daily_bars_service import DailyBarsService
from data_hub.services.event_cluster_service import EventClusterService
from data_hub.services.finance_news_service import FinanceNewsService
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.orchestrator import DecisionService
from trading.research.factor_adapters import technical_factor_output
from trading.schemas import Bar, DecisionRequest, PortfolioState


SHANGHAI = ZoneInfo("Asia/Shanghai")
EVENT_TIME = datetime(2026, 7, 20, 15, tzinfo=SHANGHAI)
FETCHED_AT = datetime(2026, 7, 20, 15, 5, tzinfo=SHANGHAI)
GENERATED_AT = datetime(2026, 7, 20, 15, 10, tzinfo=SHANGHAI)


@pytest.fixture()
def unified_database(tmp_path, monkeypatch):
    database_path = tmp_path / "unified.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", database_path)
    initialize_database()
    return database_path


@pytest.fixture()
def canonical_service(unified_database) -> CanonicalizationService:
    policy = CanonicalizationPolicy(
        market_source_priority=(
            "Tushare Pro",
            "AKShare / Eastmoney",
            "BaoStock",
        ),
        financial_source_priority=(
            "Tushare Pro",
            "CNInfo",
            "AKShare",
            "BaoStock",
        ),
        price_tolerance=0.005,
        volume_tolerance=0.01,
        financial_tolerance=0.01,
    )
    return CanonicalizationService(policy=policy)


def _market_record(
    source: str,
    record_id: str,
    *,
    close: float = 10.0,
    volume: float = 1_000_000,
) -> MarketRecord:
    payload = {
        "trade_date": "20260720",
        "open": 9.9,
        "high": 10.2,
        "low": 9.8,
        "close": close,
        "amount": 10_000_000,
    }
    if source == "Tushare Pro":
        payload["vol"] = volume / 100
        payload["amount"] = 10_000
    elif source == "AKShare / Eastmoney":
        payload["volume"] = volume / 100
    else:
        payload["volume"] = volume
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=DataType.DAILY_BAR,
        event_time=EVENT_TIME,
        fetched_at=FETCHED_AT,
        source_name=source,
        source_level=(
            SourceLevel.PUBLIC_WEB
            if source == "AKShare / Eastmoney"
            else SourceLevel.STRUCTURED
        ),
        verified=False,
        content_hash=hashlib.sha256(record_id.encode()).hexdigest(),
        data=payload,
    )


def _persist(*records: MarketRecord) -> None:
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)


def test_three_sources_create_one_canonical_market_record(
    canonical_service,
) -> None:
    records = [
        _market_record("Tushare Pro", "raw-ts"),
        _market_record("AKShare / Eastmoney", "raw-ak"),
        _market_record("BaoStock", "raw-bs"),
    ]
    _persist(*records)

    canonical = canonical_service.canonicalize_market(
        records,
        generated_at=GENERATED_AT,
    )

    assert canonical.verification_status == VerificationStatus.VERIFIED
    assert canonical.primary_source == "Tushare Pro"
    assert len(canonical.source_record_ids) == 3
    assert canonical.payload["volume"] == 1_000_000
    assert len(CanonicalMarketRepository().list()) == 1


def test_market_differences_within_tolerance_are_verified(
    canonical_service,
) -> None:
    records = [
        _market_record("Tushare Pro", "within-ts", close=10.0),
        _market_record("BaoStock", "within-bs", close=10.04),
    ]
    _persist(*records)

    canonical = canonical_service.canonicalize_market(
        records,
        generated_at=GENERATED_AT,
    )

    assert canonical.verification_status == VerificationStatus.VERIFIED
    assert canonical.confidence > 0.8


def test_market_differences_outside_tolerance_are_conflict(
    canonical_service,
) -> None:
    records = [
        _market_record("Tushare Pro", "conflict-ts", close=10.0),
        _market_record("BaoStock", "conflict-bs", close=10.20),
    ]
    _persist(*records)

    canonical = canonical_service.canonicalize_market(
        records,
        generated_at=GENERATED_AT,
    )

    assert canonical.verification_status == VerificationStatus.CONFLICT
    assert canonical.confidence == 0
    assert canonical.field_differences["conflict-bs"]["fields"]["close"][
        "reason"
    ] == "OUTSIDE_TOLERANCE"
    assert CanonicalMarketRepository().conflicts() == [canonical]


def test_single_source_is_explicit_and_missing_is_not_zero(
    canonical_service,
) -> None:
    record = _market_record("BaoStock", "single-bs")
    record = record.model_copy(
        update={"data": {**record.data, "amount": None}}
    )
    _persist(record)

    canonical = canonical_service.canonicalize_market(
        [record],
        generated_at=GENERATED_AT,
    )

    assert canonical.verification_status == VerificationStatus.SINGLE_SOURCE
    assert canonical.confidence == 0.5
    assert "amount" not in canonical.payload
    assert CanonicalMarketRepository().single_source() == [canonical]


def test_canonicalization_is_idempotent(canonical_service) -> None:
    records = [
        _market_record("Tushare Pro", "repeat-ts"),
        _market_record("BaoStock", "repeat-bs"),
    ]
    _persist(*records)

    first = canonical_service.canonicalize_market(
        records,
        generated_at=GENERATED_AT,
    )
    second = canonical_service.canonicalize_market(
        records,
        generated_at=GENERATED_AT + timedelta(hours=1),
    )

    assert first == second
    assert len(CanonicalMarketRepository().list()) == 1


def test_daily_bar_persistence_populates_canonical_layer_idempotently(
    unified_database,
) -> None:
    records = [
        _market_record("Tushare Pro", "pipeline-ts"),
        _market_record("BaoStock", "pipeline-bs"),
    ]
    service = object.__new__(DailyBarsService)

    service._persist_records(records)
    repeated_fetch = [
        record.model_copy(update={"record_id": f"repeat-{record.record_id}"})
        for record in records
    ]
    service._persist_records(repeated_fetch)

    canonical = CanonicalMarketRepository().list()
    assert len(canonical) == 1
    assert set(canonical[0].source_record_ids) == {
        "pipeline-ts",
        "pipeline-bs",
    }


def test_financial_single_source_is_persisted(unified_database) -> None:
    record = MarketRecord(
        record_id="financial-raw",
        symbol="600000.SH",
        data_type=DataType.FINANCIAL_STATEMENT,
        event_time=EVENT_TIME,
        fetched_at=FETCHED_AT,
        source_name="Tushare Pro / 利润表",
        source_level=SourceLevel.STRUCTURED,
        verified=False,
        content_hash=hashlib.sha256(b"financial-raw").hexdigest(),
        data={
            "statement_type": "income",
            "report_period": "20260630",
            "revenue": 1_000_000,
        },
    )
    _persist(record)

    canonical = CanonicalizationService().canonicalize_financial(
        [record],
        generated_at=GENERATED_AT,
    )

    assert canonical.verification_status == VerificationStatus.SINGLE_SOURCE
    assert CanonicalFinancialRepository().get(
        canonical.canonical_record_id
    ) == canonical


def test_financial_statement_subtypes_do_not_overwrite_each_other(
    unified_database,
) -> None:
    records = [
        MarketRecord(
            record_id=f"financial-{statement_type}",
            symbol="600000.SH",
            data_type=DataType.FINANCIAL_STATEMENT,
            event_time=EVENT_TIME,
            fetched_at=FETCHED_AT,
            source_name=f"Tushare Pro / {statement_type}",
            source_level=SourceLevel.STRUCTURED,
            verified=False,
            content_hash=hashlib.sha256(
                f"financial-{statement_type}".encode()
            ).hexdigest(),
            data={
                "statement_type": statement_type,
                "report_period": "20260630",
                "value": value,
            },
        )
        for statement_type, value in (
            ("income", 1_000_000),
            ("balance", 2_000_000),
        )
    ]
    _persist(*records)
    service = CanonicalizationService()

    outputs = [
        service.canonicalize_financial(
            [record],
            generated_at=GENERATED_AT,
        )
        for record in records
    ]

    assert {output.data_type for output in outputs} == {
        "financial_statement:income",
        "financial_statement:balance",
    }
    assert len(CanonicalFinancialRepository().list()) == 2


def _event_record(
    *,
    record_id: str,
    title: str,
    source_name: str,
    source_level: SourceLevel,
    event_type: DataType = DataType.ANNOUNCEMENT,
    minutes: int = 0,
) -> MarketRecord:
    event_time = EVENT_TIME + timedelta(minutes=minutes)
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=event_type,
        event_time=event_time,
        fetched_at=event_time + timedelta(minutes=5),
        source_name=source_name,
        source_level=source_level,
        verified=source_level == SourceLevel.OFFICIAL,
        content_hash=hashlib.sha256(
            f"{source_name}:{title}".encode()
        ).hexdigest(),
        data={
            "title": title,
            "symbol": "600000.SH",
            "sector": "银行",
        },
    )


def test_reposted_event_creates_one_cluster(unified_database) -> None:
    records = [
        _event_record(
            record_id="media-a",
            title="公司发布重大资产重组公告",
            source_name="Media A",
            source_level=SourceLevel.MEDIA,
        ),
        _event_record(
            record_id="media-b",
            title="公司发布重大资产重组公告！",
            source_name="Media B",
            source_level=SourceLevel.MEDIA,
            minutes=10,
        ),
    ]
    _persist(*records)

    clusters = EventClusterService().cluster(
        records,
        generated_at=GENERATED_AT + timedelta(hours=1),
    )

    assert len(clusters) == 1
    assert clusters[0].source_count == 2
    assert len(EventClusterRepository().list()) == 1


def test_news_persistence_populates_event_layer_idempotently(
    unified_database,
) -> None:
    record = _event_record(
        record_id="pipeline-news",
        title="公司发布重大事项进展",
        source_name="Media",
        source_level=SourceLevel.MEDIA,
        event_type=DataType.FINANCE_NEWS,
    )

    FinanceNewsService._persist([record])
    FinanceNewsService._persist(
        [record.model_copy(update={"record_id": "repeat-pipeline-news"})]
    )

    clusters = EventClusterRepository().list()
    assert len(clusters) == 1
    assert clusters[0].source_record_ids == ["pipeline-news"]


def test_official_event_becomes_primary_source(unified_database) -> None:
    media = _event_record(
        record_id="primary-media",
        title="公司发布重大资产重组公告！",
        source_name="Media",
        source_level=SourceLevel.MEDIA,
    )
    official = _event_record(
        record_id="primary-official",
        title="公司发布重大资产重组公告",
        source_name="CNInfo",
        source_level=SourceLevel.OFFICIAL,
        minutes=5,
    )
    _persist(media, official)

    cluster = EventClusterService().cluster(
        [media, official],
        generated_at=GENERATED_AT + timedelta(hours=1),
    )[0]

    assert cluster.primary_source_id == "primary-official"
    assert cluster.canonical_title == official.data["title"]


def test_different_events_are_not_merged(unified_database) -> None:
    records = [
        _event_record(
            record_id="event-one",
            title="公司发布年度报告",
            source_name="CNInfo",
            source_level=SourceLevel.OFFICIAL,
        ),
        _event_record(
            record_id="event-two",
            title="公司董事长发生变更",
            source_name="CNInfo",
            source_level=SourceLevel.OFFICIAL,
            minutes=5,
        ),
    ]
    _persist(*records)

    clusters = EventClusterService().cluster(
        records,
        generated_at=GENERATED_AT + timedelta(hours=1),
    )

    assert len(clusters) == 2
    assert len(EventClusterRepository().list()) == 2


def _factor(
    *,
    evidence_ids: list[str],
    score: float = 0.4,
) -> FactorOutput:
    return FactorOutput(
        factor_id="factor-test",
        symbol="600000.SH",
        factor_type=FactorType.TECHNICAL,
        score=score,
        confidence=0.8,
        data_cutoff=FETCHED_AT,
        generated_at=GENERATED_AT,
        evidence_ids=evidence_ids,
        risk_flags=[],
        model_call_ids=[],
        algorithm_version="test-v1",
        input_snapshot_hash=hashlib.sha256(b"snapshot").hexdigest(),
        shadow_mode=True,
        metadata={"structured_summary": "deterministic test factor"},
    )


def test_factor_output_score_boundary_is_enforced() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        _factor(evidence_ids=["raw"], score=1.01)


def test_factor_output_evidence_is_traceable(
    canonical_service,
) -> None:
    raw = _market_record("Tushare Pro", "factor-raw")
    _persist(raw)
    canonical = canonical_service.canonicalize_market(
        [raw],
        generated_at=GENERATED_AT,
    )
    repository = FactorOutputRepository()
    factor = _factor(
        evidence_ids=[raw.record_id, canonical.canonical_record_id]
    )

    persisted = repository.save(factor)

    assert persisted == factor
    assert repository.resolve_evidence(raw.record_id)["layer"] == "data_records"
    assert (
        repository.resolve_evidence(canonical.canonical_record_id)["layer"]
        == "canonical_market_records"
    )
    with pytest.raises(EvidenceNotFoundError):
        repository.save(
            _factor(evidence_ids=["missing-evidence"]).model_copy(
                update={"factor_id": "missing-factor"}
            )
        )


def test_technical_adapter_preserves_existing_signal_and_is_shadow(
    unified_database,
) -> None:
    records = [
        _market_record(
            "Tushare Pro",
            f"adapter-{index}",
            close=10 + index * 0.01,
        ).model_copy(
            update={
                "event_time": EVENT_TIME + timedelta(days=index),
                "fetched_at": FETCHED_AT + timedelta(days=index),
                "data": {
                    "trade_date": (
                        date(2026, 1, 1) + timedelta(days=index)
                    ).strftime("%Y%m%d"),
                    "open": 9.9 + index * 0.01,
                    "high": 10.2 + index * 0.01,
                    "low": 9.8 + index * 0.01,
                    "close": 10 + index * 0.01,
                    "volume": 1_000_000,
                },
            }
        )
        for index in range(30)
    ]
    _persist(*records)
    bars = [
        Bar(
            trade_date=date(2026, 1, 1) + timedelta(days=index),
            open=9.9 + index * 0.01,
            high=10.2 + index * 0.01,
            low=9.8 + index * 0.01,
            close=10 + index * 0.01,
            volume=1_000_000,
        )
        for index in range(30)
    ]
    factor = technical_factor_output(
        symbol="600000.SH",
        bars=bars,
        evidence_ids=[record.record_id for record in records],
        data_cutoff=max(record.fetched_at for record in records),
    )

    assert factor.factor_type == FactorType.TECHNICAL
    assert factor.shadow_mode is True
    assert -1 <= factor.score <= 1


def test_factor_output_can_be_referenced_by_immutable_decision_packet(
    unified_database,
) -> None:
    records = []
    start = date(2026, 1, 1)
    for index in range(30):
        raw = _market_record(
            "Tushare Pro",
            f"decision-factor-{index}",
            close=10 + index * 0.01,
        ).model_copy(
            update={
                "event_time": datetime.combine(
                    start + timedelta(days=index),
                    datetime.min.time(),
                    tzinfo=SHANGHAI,
                ),
                "fetched_at": datetime.combine(
                    start + timedelta(days=index),
                    datetime.min.time(),
                    tzinfo=SHANGHAI,
                ),
                "data": {
                    "trade_date": (
                        start + timedelta(days=index)
                    ).strftime("%Y%m%d"),
                    "open": 9.9 + index * 0.01,
                    "high": 10.2 + index * 0.01,
                    "low": 9.8 + index * 0.01,
                    "close": 10 + index * 0.01,
                    "volume": 1_000_000,
                },
                "verified": True,
            }
        )
        records.append(raw)
    _persist(*records)
    cutoff = max(record.event_time for record in records)
    factor = _factor(evidence_ids=[records[-1].record_id]).model_copy(
        update={
            "factor_id": "decision-factor-output",
            "data_cutoff": cutoff,
            "generated_at": cutoff + timedelta(minutes=1),
        }
    )
    FactorOutputRepository().save(factor)
    repository = DecisionRepository()
    packet = asyncio.run(
        DecisionService(repository).create_decision(
            DecisionRequest(
                symbol="600000.SH",
                bars=[
                    Bar(
                        trade_date=start + timedelta(days=index),
                        open=9.9 + index * 0.01,
                        high=10.2 + index * 0.01,
                        low=9.8 + index * 0.01,
                        close=10 + index * 0.01,
                        volume=1_000_000,
                    )
                    for index in range(30)
                ],
                portfolio=PortfolioState(
                    cash=900_000,
                    equity=1_000_000,
                ),
                evidence_refs=[
                    f"data_record:{record.record_id}"
                    for record in records
                ],
                factor_output_ids=[factor.factor_id],
            )
        )
    )

    assert packet.factor_output_ids == [factor.factor_id]
    assert packet.hash_is_valid()
    assert repository.get(packet.decision_id, 1) == packet


def test_migration_is_idempotent_on_consecutive_runs(tmp_path) -> None:
    database_path = tmp_path / "migration.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        before = len(connection.execute("SHOW TABLES").fetchall())
        first_applied = apply_migration(connection)
        after_first = len(connection.execute("SHOW TABLES").fetchall())
        first_tables = connection.execute("SHOW TABLES").fetchall()
        second_applied = apply_migration(connection)
        after_second = len(connection.execute("SHOW TABLES").fetchall())
        second_tables = connection.execute("SHOW TABLES").fetchall()

    assert first_applied is True
    assert second_applied is False
    assert before == 0
    assert after_first == 9
    assert after_second == after_first
    assert second_tables == first_tables


def test_migration_failure_rolls_back_all_changes(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "failed-migration.duckdb"
    monkeypatch.setattr(
        unified_migration,
        "MIGRATION_STATEMENTS",
        (
            "CREATE TABLE should_rollback (id INTEGER)",
            "THIS IS NOT VALID SQL",
        ),
    )
    with duckdb.connect(str(database_path)) as connection:
        with pytest.raises(duckdb.Error):
            unified_migration.apply_migration(connection)
        assert connection.execute("SHOW TABLES").fetchall() == []


def test_legacy_raw_data_survives_repeated_initialization(
    unified_database,
) -> None:
    record = _market_record("BaoStock", "legacy-raw")
    _persist(record)

    initialize_database()
    initialize_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT record_id, payload_json
            FROM data_records
            WHERE record_id = ?
            """,
            [record.record_id],
        ).fetchone()
    assert row is not None
    assert row[0] == record.record_id
