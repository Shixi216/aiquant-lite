from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from database.db import get_connection, initialize_database, insert_market_record
from trading.decision_support.decision_packets import (
    DecisionImmutableError,
    DecisionIntegrityError,
    DecisionRepository,
    SourceTraceabilityError,
)
from trading.decision_support.orchestrator import DecisionService
from trading.schemas import (
    DecisionChallengeRequest,
    DecisionRequest,
    DecisionStatus,
    ModelInference,
    PortfolioState,
    VerifiedFact,
)


def _seed_records(count: int = 30) -> list[MarketRecord]:
    records: list[MarketRecord] = []
    start = date(2025, 1, 1)
    with get_connection() as connection:
        for index in range(count):
            trade_date = start + timedelta(days=index)
            close = 10 + index * 0.05
            record = MarketRecord(
                record_id=f"packet-record-{index}",
                symbol="600000.SH",
                data_type=DataType.DAILY_BAR,
                event_time=datetime.fromisoformat(
                    f"{trade_date.isoformat()}T15:00:00+08:00"
                ),
                source_name="decision-packet-test",
                source_level=SourceLevel.STRUCTURED,
                verified=True,
                data={
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 1_000_000,
                },
            )
            insert_market_record(connection, record)
            records.append(record)
    return records


@pytest.fixture
def decision_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "decision-packets.duckdb")
    initialize_database()
    records = _seed_records()
    repository = DecisionRepository()
    service = DecisionService(repository)
    request = DecisionRequest(
        symbol="600000.SH",
        bars=[
            {
                **record.data,
                "trade_date": datetime.strptime(
                    record.data["trade_date"],
                    "%Y%m%d",
                ).date(),
            }
            for record in records
        ],
        portfolio=PortfolioState(cash=900_000, equity=1_000_000),
        evidence_refs=[
            f"data_record:{record.record_id}"
            for record in records
        ],
    )
    packet = asyncio.run(service.create_decision(request))
    return {
        "repository": repository,
        "service": service,
        "packet": packet,
        "records": records,
    }


def test_final_packet_cannot_be_modified(decision_context) -> None:
    packet = decision_context["packet"]
    repository = decision_context["repository"]

    with pytest.raises(ValidationError):
        packet.confidence = 0.1

    changed = packet.rehashed_copy(confidence=0.1)
    with pytest.raises(DecisionImmutableError):
        repository.insert(changed)


def test_new_version_does_not_overwrite_old_version(decision_context) -> None:
    packet = decision_context["packet"]
    service = decision_context["service"]
    repository = decision_context["repository"]
    with get_connection() as connection:
        old_payload_before = connection.execute(
            """
            SELECT payload_json
            FROM decision_packets
            WHERE decision_id = ? AND decision_version = 1
            """,
            [packet.decision_id],
        ).fetchone()[0]

    challenge = service.challenge(
        packet.decision_id,
        DecisionChallengeRequest(
            challenge="Re-evaluate the bullish conclusion.",
            create_new_version=True,
        ),
    )

    versions = repository.list_versions(packet.decision_id)
    with get_connection() as connection:
        old_payload_after = connection.execute(
            """
            SELECT payload_json
            FROM decision_packets
            WHERE decision_id = ? AND decision_version = 1
            """,
            [packet.decision_id],
        ).fetchone()[0]
    assert old_payload_before == old_payload_after
    assert [item.decision_version for item in versions] == [1, 2]
    assert versions[0].status == DecisionStatus.SUPERSEDED
    assert versions[1].status == DecisionStatus.FINAL
    assert versions[1].supersedes_version == 1
    assert challenge.created_version == 2


def test_packet_hash_can_be_verified(decision_context) -> None:
    packet = decision_context["packet"]
    loaded = decision_context["repository"].get(
        packet.decision_id,
        packet.decision_version,
    )

    assert packet.hash_is_valid()
    assert loaded.packet_hash == packet.packet_hash
    assert loaded.calculate_hash() == loaded.packet_hash


def test_tampered_payload_is_detected_and_audited(decision_context) -> None:
    packet = decision_context["packet"]
    repository = decision_context["repository"]
    with get_connection() as connection:
        payload = connection.execute(
            """
            SELECT payload_json
            FROM decision_packets
            WHERE decision_id = ? AND decision_version = ?
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchone()[0]
        payload_data = json.loads(payload)
        payload_data["confidence"] = 0.01
        connection.execute(
            """
            UPDATE decision_packets
            SET payload_json = ?
            WHERE decision_id = ? AND decision_version = ?
            """,
            [
                json.dumps(payload_data, ensure_ascii=False),
                packet.decision_id,
                packet.decision_version,
            ],
        )

    with pytest.raises(DecisionIntegrityError):
        repository.get(packet.decision_id, packet.decision_version)

    with get_connection() as connection:
        audit_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM decision_integrity_audit
            WHERE decision_id = ? AND decision_version = ?
              AND event_type = 'INTEGRITY_FAILURE'
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchone()[0]
    assert audit_count == 1


def test_tampered_evidence_snapshot_is_detected(decision_context) -> None:
    packet = decision_context["packet"]
    repository = decision_context["repository"]
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE decision_evidence
            SET verified_snapshot = FALSE
            WHERE decision_id = ? AND decision_version = ?
              AND source_record_id = ?
            """,
            [
                packet.decision_id,
                packet.decision_version,
                packet.source_record_ids[0],
            ],
        )

    with pytest.raises(DecisionIntegrityError, match="source snapshots"):
        repository.get(packet.decision_id, packet.decision_version)


def test_challenge_does_not_modify_old_version(decision_context) -> None:
    packet = decision_context["packet"]
    service = decision_context["service"]
    repository = decision_context["repository"]
    before = repository.get(packet.decision_id, 1)

    result = service.challenge(
        packet.decision_id,
        DecisionChallengeRequest(
            challenge="What if the trend signal is a false breakout?",
            create_new_version=False,
        ),
    )

    after = repository.get(packet.decision_id, 1)
    assert before == after
    assert result.created_version is None
    assert result.new_packet is None
    assert result.recommendation_only is True
    assert len(repository.list_versions(packet.decision_id)) == 1
    with get_connection() as connection:
        challenge_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM adversarial_reviews
            WHERE decision_id = ? AND review_kind = 'CHALLENGE'
            """,
            [packet.decision_id],
        ).fetchone()[0]
    assert challenge_count == 1


def test_decision_service_has_no_simulation_repository_dependency() -> None:
    signature = inspect.signature(DecisionService.__init__)
    assert list(signature.parameters) == ["self", "repository"]
    source = inspect.getsource(DecisionService)
    assert "SimulationRepository" not in source
    assert "ManualTradeRepository" not in source
    assert "SimulationService" not in source
    assert "PaperTradingEngine" not in source


def test_data_cutoff_time_cannot_be_later_than_generation(decision_context) -> None:
    packet = decision_context["packet"]
    with pytest.raises(ValidationError, match="data_cutoff_time"):
        packet.rehashed_copy(
            data_cutoff_time=packet.generated_at + timedelta(seconds=1)
        )


def test_challenge_cutoff_cannot_precede_its_evidence(decision_context) -> None:
    packet = decision_context["packet"]
    service = decision_context["service"]
    with pytest.raises(ValueError, match="every referenced source"):
        service.challenge(
            packet.decision_id,
            DecisionChallengeRequest(
                challenge="Review using an invalid historical cutoff.",
                data_cutoff_time=packet.data_cutoff_time - timedelta(seconds=1),
            ),
        )


def test_source_record_ids_must_be_traceable(decision_context) -> None:
    packet = decision_context["packet"]
    repository = decision_context["repository"]
    untraceable = packet.rehashed_copy(
        decision_id="untraceable-decision",
        source_record_ids=["missing-record"],
        verified_facts=[],
        model_inferences=[
            inference.model_copy(update={"supporting_source_record_ids": []})
            for inference in packet.model_inferences
        ],
    )

    with pytest.raises(SourceTraceabilityError, match="missing-record"):
        repository.insert(untraceable)


def test_model_inferences_and_verified_facts_cannot_be_mixed() -> None:
    now = datetime.now().astimezone()
    with pytest.raises(ValidationError):
        ModelInference.model_validate(
            {
                "fact": "verified close",
                "value": 10.0,
                "as_of": now,
                "source_record_ids": ["record-1"],
            }
        )
    with pytest.raises(ValidationError):
        VerifiedFact.model_validate(
            {
                "agent_role": "technical_agent",
                "inference": "trend is bullish",
                "confidence": 0.8,
                "supporting_source_record_ids": ["record-1"],
            }
        )


def test_decision_packet_uses_composite_primary_key(decision_context) -> None:
    with get_connection() as connection:
        columns = connection.execute(
            "PRAGMA table_info('decision_packets')"
        ).fetchall()
    primary_key_columns = [
        row[1]
        for row in sorted(columns, key=lambda row: row[5])
        if row[5] > 0
    ]
    assert primary_key_columns == ["decision_id", "decision_version"]
